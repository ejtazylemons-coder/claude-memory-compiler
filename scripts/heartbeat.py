"""Shared heartbeat emitter — Phase 2 Memory Spine.

Any pipeline stage can import this module and call `emit()` to write the
canonical spine heartbeat JSON locally and push it to Homebase.

Beacon shape (matches the Ops `beacon_healthy` check contract):
  {
    "name":      <str>   stage/slug name,
    "machine":   <str>   hostname,
    "last_run":  <str>   ISO-8601 UTC timestamp,
    "exit_code": <int>   0=ok, 1=fail, 2=warn,
    "summary":   <str>   short human-readable status line (≤240 chars),
    ... any extra kv pairs the caller wants to attach
  }

Usage
-----
    from heartbeat import emit

    emit(
        slug="claude-memory-reconciler",
        local_path=Path("scripts/reconcile.beacon.json"),   # laptop-local
        remote_path="/root/hestia/beacons/claude-memory-reconciler.json",
        exit_code=0,
        summary="5 rows, 0 failing — GREEN",
    )

The remote_path is best-effort (SSH pipe); a push failure does NOT change the
exit code — the Ops worker will catch the resulting staleness on its next cycle.

Which stages should adopt this helper (once they own their beacon writes):
  - scripts/reconcile.py (write_verdict path already wired; add SSH push leg)
  - scripts/compile.py (currently no beacon; would prove compilation ran)
  - scripts/dream.py (currently no beacon; would prove hygiene ran)
  - scripts/flush.py (per-session; beacon already pushed via session-end hook)
"""
from __future__ import annotations

import json
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(
    slug: str,
    local_path: Path,
    remote_path: str,
    exit_code: int,
    summary: str,
    **extra,
) -> None:
    """Write a heartbeat beacon locally and push it to Homebase via SSH.

    Parameters
    ----------
    slug:
        The Ops worker slug (e.g. ``"claude-memory-reconciler"``).
    local_path:
        Laptop-local path for the beacon JSON. Written first; used by the
        local advisory reconciler run.
    remote_path:
        Absolute path on Homebase (e.g.
        ``"/root/hestia/beacons/claude-memory-reconciler.json"``).
        This is what the Daily Monitor's ``beacon_healthy`` check reads.
    exit_code:
        0 = ok, 1 = fail, 2 = warn (matches Ops beacon_healthy contract).
    summary:
        Short status line (≤240 chars — Ops 280-char cap, headroom for prefix).
    **extra:
        Any additional key-value pairs to attach to the beacon payload (e.g.
        ``failing=["row1"]``, ``rows_checked=5``).
    """
    payload: dict = {
        "name": slug,
        "machine": socket.gethostname(),
        "last_run": _now_iso(),
        "exit_code": exit_code,
        "summary": summary[:240],
    }
    payload.update(extra)

    # ── local write ──────────────────────────────────────────────────────
    try:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"  heartbeat: local write failed (non-fatal): {e}", file=sys.stderr)

    # ── Homebase push via SSH stdin pipe ─────────────────────────────────
    try:
        body = json.dumps(payload, separators=(",", ":"))
        proc = subprocess.Popen(
            ["ssh", "homebase", f"cat > {shlex.quote(remote_path)}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc.communicate(input=body.encode("utf-8"), timeout=20)
    except Exception as e:
        # Push failure must NOT change the caller's exit code. The Ops worker
        # will catch the staleness on its next cycle.
        print(
            f"  heartbeat: SSH push failed (non-fatal): {type(e).__name__}: {e}",
            file=sys.stderr,
        )
