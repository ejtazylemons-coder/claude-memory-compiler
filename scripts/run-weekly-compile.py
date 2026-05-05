"""Single weekly entry point: rollup → compile → verify → beacon.

Replaces the prior bat-file model where weekly-rollup.py ran alone and
compile.py was unwired (so the wiki at concepts/ + connections/ + qa/
stopped growing 2026-04-14).

Safety rails (all configurable in budget_guard.py):
- Combined monthly hard cap ($4.00) — auto-disables compiler if exceeded.
- Soft warn at $3.00 — Telegram nudge, keep running.
- Silent-zero detector — auto-disables on 2 consecutive cost=$0 + no-output runs.
- Disabled flag — once tripped, all future runs refuse until manual `--enable`.

Beacon shape (consumed by Ops worker `claude-weekly-compile` via beacon_healthy):
  exit_code=0 → ok (clean run)
  exit_code=1 → fail (real error — Daily Monitor will escalate)
  exit_code=2 → warn (auto-disabled or soft-warn — surfaces but doesn't escalate)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import budget_guard
from config import DAILY_DIR, KB_ROOT, SCRIPTS_DIR

WEEKLY_DIR = KB_ROOT / "weekly"
BEACON_LOCAL = SCRIPTS_DIR / "weekly-compile.beacon.json"
BEACON_REMOTE = "/root/hestia/beacons/claude-weekly-compile.json"
ROLLUP_SCRIPT = SCRIPTS_DIR / "weekly-rollup.py"
COMPILE_SCRIPT = SCRIPTS_DIR / "compile.py"
VENV_PYTHON = SCRIPTS_DIR.parent / ".venv" / "Scripts" / "python.exe"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _expected_weekly_filename() -> Path:
    iso_year, iso_week, _ = date.today().isocalendar()
    return WEEKLY_DIR / f"{iso_year}-W{iso_week:02d}.md"


def _list_uncompiled_dailies() -> list[Path]:
    """Return daily/.md files that compile.py hasn't ingested yet (last 14 days).

    14-day window is intentional buffer: if a week is skipped due to monthly-cap
    or disable, the next run will still pick up the missed days.
    """
    state_path = SCRIPTS_DIR / "state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    ingested = state.get("ingested", {})

    cutoff = date.today() - timedelta(days=14)
    out = []
    for p in sorted(DAILY_DIR.glob("*.md")):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        if p.name in ingested:
            continue
        out.append(p)
    return out


def _push_beacon(payload: dict) -> None:
    """Write local beacon + push to Homebase. Best-effort on the SSH leg."""
    BEACON_LOCAL.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        body = json.dumps(payload, separators=(",", ":"))
        # Use ssh stdin pipe — same pattern as session-end.py
        proc = subprocess.Popen(
            ["ssh", "homebase", f"cat > {BEACON_REMOTE}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc.communicate(input=body.encode("utf-8"), timeout=20)
    except Exception as e:
        # Beacon push failure must not change exit code — Ops worker will catch
        # the staleness on its next cycle.
        print(f"  Beacon push failed (non-fatal): {type(e).__name__}: {e}")


def _run_subprocess(script: Path, *args: str) -> tuple[int, str]:
    """Run a script via venv python. Returns (exit_code, captured_output)."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # Windows cp1252 console fix
    cmd = [str(VENV_PYTHON), str(script), *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(SCRIPTS_DIR.parent),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30 * 60,  # 30 min per phase
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after 30 minutes"
    except Exception as e:
        return 1, f"subprocess failed: {type(e).__name__}: {e}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No API calls; print what would run")
    parser.add_argument("--rollup-only", action="store_true", help="Skip compile phase")
    parser.add_argument("--compile-only", action="store_true", help="Skip rollup phase")
    parser.add_argument("--no-beacon", action="store_true", help="Skip Homebase beacon push")
    args = parser.parse_args()

    started_at = _now_iso()
    machine = socket.gethostname()
    print(f"=== Weekly compile run @ {started_at} on {machine} ===")

    # ── Pre-flight: disabled flag ──────────────────────────────────────
    disabled, reason = budget_guard.is_disabled()
    if disabled:
        print(f"REFUSING: compiler is disabled — {reason}")
        print("Re-enable: python scripts/budget_guard.py --enable")
        if not args.no_beacon:
            _push_beacon({
                "name": "ClaudeWeeklyCompile",
                "machine": machine,
                "last_run": started_at,
                "exit_code": 2,  # warn — Ops surfaces but doesn't escalate
                "summary": f"disabled: {reason}",
                "disabled": True,
            })
        return 2  # warn / disabled

    # ── Pre-flight: budget ─────────────────────────────────────────────
    remaining, soft_warn, hard_cap = budget_guard.check_remaining()
    budget = budget_guard.load_combined_budget()
    print(f"Budget: ${budget['spent']:.4f} spent this month, ${remaining:.4f} remaining")

    if hard_cap:
        msg = (
            f"Monthly hard cap ${budget_guard.MONTHLY_HARD_CAP_USD} hit "
            f"(spent ${budget['spent']:.4f}). Auto-disabling compiler."
        )
        print(f"FAIL: {msg}")
        budget_guard.disable(f"hard-cap hit: ${budget['spent']:.4f} >= ${budget_guard.MONTHLY_HARD_CAP_USD}")
        budget_guard.telegram_alert(f"Memory compiler AUTO-DISABLED on {machine}: {msg}")
        if not args.no_beacon:
            _push_beacon({
                "name": "ClaudeWeeklyCompile",
                "machine": machine,
                "last_run": started_at,
                "exit_code": 2,
                "summary": "auto-disabled: hard-cap",
                "disabled": True,
                "monthly_spent": budget["spent"],
            })
        return 2

    if soft_warn:
        budget_guard.telegram_alert(
            f"Memory compiler soft warn on {machine}: ${budget['spent']:.4f} of "
            f"${budget_guard.MONTHLY_HARD_CAP_USD} cap used. Hard cap auto-disables at "
            f"${budget_guard.MONTHLY_HARD_CAP_USD}."
        )

    if args.dry_run:
        rollup_target = _expected_weekly_filename()
        uncompiled = _list_uncompiled_dailies()
        print(f"[dry run] Would write rollup to: {rollup_target}")
        print(f"[dry run] Would compile {len(uncompiled)} daily file(s):")
        for p in uncompiled[:10]:
            print(f"  - {p.name}")
        if len(uncompiled) > 10:
            print(f"  ... and {len(uncompiled) - 10} more")
        return 0

    # ── Phase 1: weekly rollup ─────────────────────────────────────────
    rollup_exit = 0
    rollup_output_existed = False
    rollup_target = _expected_weekly_filename()
    if not args.compile_only:
        print(f"\n--- Phase 1: weekly rollup ---")
        existed_before = rollup_target.exists()
        existed_before_mtime = rollup_target.stat().st_mtime if existed_before else 0
        rollup_exit, rollup_log = _run_subprocess(ROLLUP_SCRIPT)
        print(rollup_log)
        # Verify the file was actually written/touched in this run
        rollup_output_existed = (
            rollup_target.exists()
            and (rollup_target.stat().st_mtime > existed_before_mtime + 1)
        )
        if rollup_exit != 0:
            print(f"  rollup exited {rollup_exit}")

    # ── Phase 2: compile uncompiled dailies ────────────────────────────
    # Per-run file cap derived from remaining budget so a single run can't
    # blow the monthly cap on a large backlog. compile.py charges up to
    # ~$0.50 per file, so allow at most floor(remaining / 0.55) files,
    # additionally capped at MAX_FILES_PER_RUN for sanity.
    MAX_FILES_PER_RUN = 5
    compile_exit = 0
    compile_count_before = 0
    compile_count_after = 0
    if not args.rollup_only:
        # Re-read remaining after rollup may have spent some
        remaining_after_rollup, _, _ = budget_guard.check_remaining()
        budget_file_limit = max(0, int(remaining_after_rollup / 0.55))
        per_run_limit = min(MAX_FILES_PER_RUN, budget_file_limit)
        uncompiled = _list_uncompiled_dailies()
        compile_count_before = len(uncompiled)
        if not uncompiled:
            print("\n--- Phase 2: nothing to compile ---")
        elif per_run_limit <= 0:
            print(f"\n--- Phase 2: SKIPPED — remaining budget ${remaining_after_rollup:.4f} < $0.55 per file ---")
            compile_exit = 0  # not a failure, just no headroom
        else:
            print(f"\n--- Phase 2: compile {min(len(uncompiled), per_run_limit)} of {len(uncompiled)} backlog file(s) (per-run cap={per_run_limit}) ---")
            compile_exit, compile_log = _run_subprocess(COMPILE_SCRIPT, "--limit", str(per_run_limit))
            print(compile_log)
        compile_count_after = len(_list_uncompiled_dailies())

    # ── Verify + record ────────────────────────────────────────────────
    rollup_ok = args.compile_only or (rollup_exit == 0 and rollup_output_existed)
    compile_ok = args.rollup_only or (compile_exit == 0 and compile_count_after <= compile_count_before)

    overall_ok = rollup_ok and compile_ok

    # Compute this-run cost from the budget files (rollup + compile each track
    # their own; combined-budget will be advanced via record_run by both).
    # For simplicity here we read the last entries of each.
    this_run_cost = 0.0
    rollup_budget_path = SCRIPTS_DIR / "weekly-budget.json"
    if rollup_ok and rollup_budget_path.exists():
        try:
            rb = json.loads(rollup_budget_path.read_text(encoding="utf-8"))
            runs = rb.get("runs", [])
            if runs:
                this_run_cost += float(runs[-1].get("cost", 0))
        except (json.JSONDecodeError, OSError):
            pass
    state_path = SCRIPTS_DIR / "state.json"
    if compile_ok and state_path.exists():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            # Walk the most-recently-compiled entries (rough approximation)
            ingested = st.get("ingested", {})
            recent = sorted(ingested.values(), key=lambda x: x.get("compiled_at", ""), reverse=True)
            for entry in recent[: (compile_count_before - compile_count_after)]:
                this_run_cost += float(entry.get("cost_usd", 0))
        except (json.JSONDecodeError, OSError):
            pass

    # Silent-zero detector: did we run a phase that should have cost something
    # AND finish with $0 reported AND no progress?
    silent_zero_detected = False
    if not args.compile_only and rollup_exit == 0 and not rollup_output_existed:
        silent_zero_detected = True
        print("WARN: rollup exited 0 but output file was not created — silent failure")
    if not args.rollup_only and compile_exit == 0 and compile_count_before > 0 and compile_count_after == compile_count_before:
        silent_zero_detected = True
        print("WARN: compile exited 0 but no daily files were ingested — silent failure")

    if silent_zero_detected:
        count = budget_guard.record_silent_zero()
        print(f"  Silent-zero counter: {count}/{budget_guard.MAX_CONSECUTIVE_SILENT_ZEROS}")
        if count >= budget_guard.MAX_CONSECUTIVE_SILENT_ZEROS:
            budget_guard.disable(f"silent-zero x{count}")
            budget_guard.telegram_alert(
                f"Memory compiler AUTO-DISABLED on {machine}: {count} consecutive "
                f"silent-zero runs. Investigate before re-enabling."
            )
            print(f"AUTO-DISABLED after {count} consecutive silent zeros")
    elif overall_ok:
        budget_guard.reset_silent_zero()

    # Record into combined budget
    outputs_written = []
    if rollup_ok and rollup_output_existed:
        outputs_written.append(str(rollup_target))
    if compile_ok and compile_count_after < compile_count_before:
        outputs_written.append(f"compile:{compile_count_before - compile_count_after}-files")
    budget_guard.record_run(
        script_name="run-weekly-compile",
        cost=this_run_cost,
        output_files=outputs_written,
    )
    final_budget = budget_guard.load_combined_budget()

    # ── Determine exit code + push beacon ──────────────────────────────
    if overall_ok and not silent_zero_detected:
        exit_code = 0
        summary = (
            f"rollup={rollup_ok} compile={compile_ok} cost=${this_run_cost:.4f} "
            f"month=${final_budget['spent']:.4f}"
        )
    elif silent_zero_detected:
        exit_code = 2  # warn
        summary = "silent-zero detected — see local log"
    else:
        exit_code = 1
        summary = (
            f"FAIL rollup_exit={rollup_exit} compile_exit={compile_exit} "
            f"rollup_output={rollup_output_existed}"
        )

    print(f"\nFinal: exit={exit_code} {summary}")

    if not args.no_beacon:
        _push_beacon({
            "name": "ClaudeWeeklyCompile",
            "machine": machine,
            "last_run": started_at,
            "exit_code": exit_code,
            "summary": summary[:240],  # Ops 280-char cap, leave headroom
            "disabled": budget_guard.is_disabled()[0],
            "rollup_ok": rollup_ok,
            "compile_ok": compile_ok,
            "outputs_written": outputs_written,
            "this_run_cost": round(this_run_cost, 4),
            "monthly_spent": final_budget["spent"],
            "monthly_cap": budget_guard.MONTHLY_HARD_CAP_USD,
        })

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
