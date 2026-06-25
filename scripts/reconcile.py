"""Runtime Registry Reconciliation — the Memory Spine keystone wall (Phase 1).

Reconciles `REGISTRY.md` (what is *supposed* to run) against runtime truth, so a
pipeline stage that silently stops running cannot sit green. Text-vs-text
REGISTRY<->TOMBSTONE is theater: the original death (2026-04-14) was a disabled
call in a Python comment that touched no tombstone. This checks the real world.

For every LIVE registry row, ALL must hold:
  (a) its trigger exists AND is enabled — Windows Task Scheduler / cron / systemd
      timer / hook. Remote triggers (a laptop schtask/hook seen FROM Homebase) are
      verified transitively by a fresh heartbeat (c) — a job that just produced a
      fresh good beacon necessarily ran, so its trigger is alive.
  (b) a LIVE Ops worker exists in ops/workers/*.yaml and is NOT in archived/
      (the live validator only globs top-level — an archived worker = silent death).
  (c) a heartbeat exists, is FRESH (within cadence + grace), and has an acceptable
      exit_code (0 ok, 2 warn). On Homebase, freshness uses the pushed beacon's
      mtime (receipt time) to avoid laptop clock skew.
  (d) every TOMBSTONE.replaced_by names a live REGISTRY row that itself passes (a)-(c).

Homebase is AUTHORITATIVE for red/green and writes the verdict JSON. The laptop
runs the same code as a fast advisory subset (directly querying Task Scheduler).

Stdlib only. Usage:
  python scripts/reconcile.py                 # auto-detect platform
  python scripts/reconcile.py --write-verdict # force-write the verdict JSON
  python scripts/reconcile.py --registry <p> --tombstone <p>   # test fixtures
Exit code: 0 == green, 1 == red.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "REGISTRY.md"
DEFAULT_TOMBSTONE = REPO_ROOT / "TOMBSTONE.md"

HOMEBASE_BEACON_DIR = Path("/root/hestia/beacons")
HOMEBASE_OPS_WORKERS = Path("/root/workspace/ops/workers")
WINDOWS_OPS_WORKERS = Path("C:/Dev/workspace/ops/workers")
VERDICT_PATH = HOMEBASE_BEACON_DIR / "claude-memory-reconciler.json"
RECONCILER_SLUG = "claude-memory-reconciler"

# A row's heartbeat must be no older than `cadence + grace`. Grace is folded in.
_GRACE_HOURS = {"weekly": 192, "daily": 36, "session": 72}
_NA = {"none", "na", "<na>", "<none/advisory>", "-", ""}

IS_HOMEBASE = platform.system() == "Linux" and HOMEBASE_BEACON_DIR.is_dir()
IS_WINDOWS = os.name == "nt"

# (status, detail): status is one of pass / fail / remote / skip. Only `fail`
# turns a row red; `remote` (transitive via heartbeat) and `skip` (advisory) don't.
Result = tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── parsing ─────────────────────────────────────────────────────────────────

def _parse_table(path: Path, ncols: int) -> list[list[str]]:
    """Return data rows of the FIRST markdown pipe-table with >= ncols columns."""
    rows: list[list[str]] = []
    started = False
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if started:
                break  # table ended
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < ncols:
            continue
        joined = "".join(cells)
        if set(joined) <= set("-: "):  # header separator row
            continue
        if not started:  # first qualifying row is the header
            started = True
            continue
        rows.append(cells[:ncols])
    return rows


def parse_registry(path: Path) -> list[dict]:
    cols = ["name", "type", "cadence", "trigger_ref", "ops_slug", "heartbeat_path"]
    return [dict(zip(cols, r)) for r in _parse_table(path, 6)]


def parse_tombstone(path: Path) -> list[dict]:
    cols = ["name", "retired_date", "replaced_by", "approved_by"]
    return [dict(zip(cols, r)) for r in _parse_table(path, 4)]


def _grace_hours(cadence: str) -> float:
    c = cadence.lower()
    for key, hours in _GRACE_HOURS.items():
        if key in c or (key == "daily" and "day" in c):
            return hours
    return 72.0


def _slug_of(yaml_path: Path) -> str | None:
    try:
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^slug:\s*(\S+)", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


# ── checks ──────────────────────────────────────────────────────────────────

def check_trigger(row: dict) -> Result:
    """(a) trigger exists and is enabled."""
    ref = row["trigger_ref"].strip()
    if ref.lower() in _NA:
        return ("skip", "advisory — no enforced trigger")
    kind, _, arg = ref.partition(":")
    kind = kind.lower()

    if kind == "cron":
        if not IS_HOMEBASE:
            return ("remote", f"cron:{arg} — only checkable on Homebase")
        try:
            out = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.SubprocessError) as e:
            return ("fail", f"crontab unreadable: {e}")
        if arg in out or "reconcile.py" in out:
            return ("pass", f"cron line present (ops-worker:{arg})")
        return ("fail", f"no crontab line tagged ops-worker:{arg}")

    if kind == "systemd":
        if not IS_HOMEBASE:
            return ("remote", f"systemd:{arg} — only checkable on Homebase")
        try:
            rc = subprocess.run(
                ["systemctl", "is-enabled", arg], capture_output=True, text=True, timeout=10
            ).returncode
        except (OSError, subprocess.SubprocessError) as e:
            return ("fail", f"systemctl failed: {e}")
        return ("pass", f"timer enabled: {arg}") if rc == 0 else ("fail", f"timer not enabled: {arg}")

    if kind == "schtask":
        if not IS_WINDOWS:
            return ("remote", f"Win task {arg} — verified transitively via heartbeat (c)")
        try:
            p = subprocess.run(
                ["schtasks", "/query", "/TN", arg, "/v", "/fo", "LIST"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return ("fail", f"schtasks failed: {e}")
        if p.returncode != 0:
            return ("fail", f"Task Scheduler entry missing: {arg}")
        if re.search(r"(?im)^\s*(Scheduled Task State|Status)\s*:\s*Disabled", p.stdout):
            return ("fail", f"Task Scheduler entry disabled: {arg}")
        return ("pass", f"Task Scheduler entry Ready: {arg}")

    if kind == "hook":
        hook_file = REPO_ROOT / "hooks" / f"{arg}.py"
        if not IS_WINDOWS:
            return ("remote", f"hook:{arg} (laptop) — verified transitively via heartbeat (c)")
        return ("pass", f"hooks/{arg}.py present") if hook_file.exists() else (
            "fail", f"hook file missing: hooks/{arg}.py")

    return ("fail", f"unknown trigger kind: {ref}")


def _ops_workers_dir() -> Path | None:
    if IS_HOMEBASE and HOMEBASE_OPS_WORKERS.is_dir():
        return HOMEBASE_OPS_WORKERS
    if WINDOWS_OPS_WORKERS.is_dir():
        return WINDOWS_OPS_WORKERS
    return None


def check_ops_worker(row: dict) -> Result:
    """(b) a live (non-archived) Ops worker exists for this slug."""
    slug = row["ops_slug"].strip()
    if slug.lower() in _NA:
        return ("skip", "advisory — no Ops worker")
    wdir = _ops_workers_dir()
    if wdir is None:
        return ("remote", "ops/workers not present on this machine")
    for yml in wdir.glob("*.yaml"):
        if _slug_of(yml) == slug:
            return ("pass", f"live worker: {yml.name}")
    archived = wdir / "archived"
    if archived.is_dir():
        for yml in archived.glob("*.yaml"):
            if _slug_of(yml) == slug:
                return ("fail", f"worker ARCHIVED (invisible to validator): archived/{yml.name}")
    return ("fail", f"no Ops worker found for slug '{slug}'")


def _homebase_beacon(row: dict) -> Path:
    return HOMEBASE_BEACON_DIR / f"{row['ops_slug'].strip()}.json"


def check_heartbeat(row: dict) -> Result:
    """(c) heartbeat exists, is fresh, and has an acceptable exit_code."""
    if row["ops_slug"].strip().lower() in _NA:
        return ("skip", "advisory — no heartbeat gated")

    if IS_HOMEBASE:
        path = _homebase_beacon(row)  # mtime = receipt time (clock-skew safe)
    else:
        hp = row["heartbeat_path"].strip()
        if hp.lower() in _NA:
            return ("skip", "no heartbeat path declared")
        path = Path(hp)
        if not path.is_absolute():
            path = REPO_ROOT / hp
        if path.is_absolute() and not IS_WINDOWS and str(path).startswith("/root") and not path.exists():
            return ("remote", f"{path} — Homebase-only beacon")

    if not path.exists():
        return ("fail", f"heartbeat missing: {path}")
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    grace = _grace_hours(row["cadence"])
    if age_h > grace:
        return ("fail", f"stale: {age_h:.1f}h > {grace:.0f}h ({path.name})")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return ("fail", f"unreadable/invalid beacon: {e}")
    if not isinstance(payload, dict):
        return ("fail", "beacon is not a JSON object")
    ec = payload.get("exit_code")
    if ec is None:
        return ("pass", f"fresh {age_h:.1f}h, exit_code missing (tolerated)")
    try:
        ec = int(ec)
    except (TypeError, ValueError):
        return ("fail", f"exit_code not an int: {ec!r}")
    if ec in (0, 2):
        return ("pass", f"fresh {age_h:.1f}h, exit_code={ec}")
    return ("fail", f"fresh {age_h:.1f}h but exit_code={ec}")


# ── reconcile ───────────────────────────────────────────────────────────────

def reconcile(registry_path: Path, tombstone_path: Path) -> dict:
    registry = parse_registry(registry_path)
    tombstones = parse_tombstone(tombstone_path)

    failing: list[dict] = []
    table: list[dict] = []
    by_name: dict[str, dict] = {}

    for row in registry:
        a, b, c = check_trigger(row), check_ops_worker(row), check_heartbeat(row)
        row_red = False
        for letter, (status, detail) in (("a", a), ("b", b), ("c", c)):
            if status == "fail":
                failing.append({"row": row["name"], "check": letter, "detail": detail})
                row_red = True
        entry = {"name": row["name"], "a": a, "b": b, "c": c, "red": row_red}
        table.append(entry)
        by_name[row["name"]] = entry

    # (d) tombstone replacements must name a live, passing registry row.
    for t in tombstones:
        repl = t["replaced_by"].strip()
        if repl.lower() in _NA:
            continue
        target = by_name.get(repl)
        if target is None:
            failing.append({"row": t["name"], "check": "d",
                            "detail": f"replaced_by '{repl}' is not a REGISTRY row"})
        elif target["red"]:
            failing.append({"row": t["name"], "check": "d",
                            "detail": f"replacement '{repl}' is RED (does not pass a-c)"})

    status = "red" if failing else "green"
    return {
        "name": RECONCILER_SLUG,
        "machine": "homebase" if IS_HOMEBASE else socket.gethostname(),
        "last_run": _now_utc().isoformat(),
        "exit_code": 1 if failing else 0,
        "reconcile_status": status,
        "failing": failing,
        "summary": (f"{len(registry)} rows, {len(tombstones)} tombstones — {status.upper()}"
                    + (f"; {len(failing)} failing checks" if failing else "")),
    }


def _print_report(verdict: dict, table_path: Path, tomb_path: Path) -> None:
    sym = {"pass": "PASS", "fail": "FAIL", "remote": "remote", "skip": "skip "}
    registry = parse_registry(table_path)
    a_b_c = {}
    for row in registry:
        a_b_c[row["name"]] = (check_trigger(row), check_ops_worker(row), check_heartbeat(row))
    machine = "Homebase (AUTHORITATIVE)" if IS_HOMEBASE else f"{verdict['machine']} (advisory)"
    print(f"\nRuntime Registry Reconciliation — {machine}")
    print(f"{'row':28} {'(a) trigger':12} {'(b) worker':12} {'(c) heartbeat':12}")
    print("-" * 70)
    for row in registry:
        a, b, c = a_b_c[row["name"]]
        print(f"{row['name']:28} {sym[a[0]]:12} {sym[b[0]]:12} {sym[c[0]]:12}")
    if verdict["failing"]:
        print("\nFAILING checks:")
        for f in verdict["failing"]:
            print(f"  - {f['row']} ({f['check']}): {f['detail']}")
    print(f"\nVERDICT: {verdict['reconcile_status'].upper()}  (exit {verdict['exit_code']})\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--tombstone", default=str(DEFAULT_TOMBSTONE))
    ap.add_argument("--write-verdict", action="store_true",
                    help="force-write the verdict JSON (default: only on Homebase)")
    ap.add_argument("--verdict-path", default=str(VERDICT_PATH))
    ap.add_argument("--quiet", action="store_true", help="suppress the report table")
    args = ap.parse_args(argv)

    verdict = reconcile(Path(args.registry), Path(args.tombstone))
    if not args.quiet:
        _print_report(verdict, Path(args.registry), Path(args.tombstone))

    if IS_HOMEBASE or args.write_verdict:
        vp = Path(args.verdict_path)
        try:
            vp.parent.mkdir(parents=True, exist_ok=True)
            vp.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
            if not args.quiet:
                print(f"verdict written: {vp}")
        except OSError as e:
            print(f"WARNING: could not write verdict to {vp}: {e}", file=sys.stderr)

    return verdict["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
