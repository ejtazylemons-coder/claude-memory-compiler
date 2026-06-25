"""Pre-commit registry guard — FAST, LOCAL, ADVISORY (Memory Spine Phase 1).

This is NOT the wall. The authoritative reconciliation runs on Homebase
(`scripts/reconcile.py`, which writes `/root/hestia/beacons/claude-memory-reconciler.json`,
read by Ops + sync-up). This hook is a fast local guard that catches obvious
REGISTRY/TOMBSTONE mistakes at commit time — but it is bypassable >=6 ways
(`git commit --no-verify`, `git stash`, MCP switch, editing .git/hooks, etc.),
so it can NEVER be the sole enforcement.

Behavior:
  - If neither REGISTRY.md nor TOMBSTONE.md is staged in this commit -> no-op (fast exit 0).
  - If either is staged -> run the local advisory subset of reconcile.py and PRINT the report.
  - HARD-BLOCK (exit 1) only on runtime-INDEPENDENT structural mistakes that are
    certain regardless of which machine runs:
      * REGISTRY.md / TOMBSTONE.md fails to parse, OR
      * a TOMBSTONE.replaced_by names a row that is absent from REGISTRY entirely
        (the exact "replaced by X, X never declared" failure, catchable at commit time).
  - Everything else (archived worker, stale/bad heartbeat, missing scheduler entry) is a
    runtime truth the Homebase reconciler owns -> print a WARNING, exit 0.

Bypass (documented, intentional): `git commit --no-verify`. The Homebase reconciler
will still flip RED and Ops/sync-up will surface it — the laptop guard is advisory only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

WATCHED = {"REGISTRY.md", "TOMBSTONE.md"}


def _staged_files() -> set[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {Path(line.strip()).name for line in out.splitlines() if line.strip()}


def main() -> int:
    if not (_staged_files() & WATCHED):
        return 0  # neither registry file touched — nothing to guard

    try:
        import reconcile  # noqa: E402  (path injected above)
    except Exception as e:  # pragma: no cover
        print(f"[registry-guard] could not import reconcile.py: {e}", file=sys.stderr)
        return 0  # don't block on a broken guard — advisory only

    reg, tomb = REPO_ROOT / "REGISTRY.md", REPO_ROOT / "TOMBSTONE.md"
    try:
        verdict = reconcile.reconcile(reg, tomb)
    except Exception as e:
        print(f"[registry-guard] BLOCK: REGISTRY/TOMBSTONE failed to parse: {e}", file=sys.stderr)
        return 1  # structural — certain regardless of runtime

    reconcile._print_report(verdict, reg, tomb)

    # Hard-block only on the runtime-independent structural mistake: a tombstone
    # pointing at a replacement that is not even declared in REGISTRY.
    structural = [
        f for f in verdict["failing"]
        if f["check"] == "d" and "is not a REGISTRY row" in f["detail"]
    ]
    if structural:
        print("\n[registry-guard] BLOCK — fix before committing:", file=sys.stderr)
        for f in structural:
            print(f"  - {f['row']}: {f['detail']}", file=sys.stderr)
        print("  (override with `git commit --no-verify` if you know what you're doing)", file=sys.stderr)
        return 1

    if verdict["failing"]:
        print("\n[registry-guard] ADVISORY WARNING — runtime checks failing (Homebase is "
              "authoritative; this guard does not block):", file=sys.stderr)
        for f in verdict["failing"]:
            print(f"  - {f['row']} ({f['check']}): {f['detail']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
