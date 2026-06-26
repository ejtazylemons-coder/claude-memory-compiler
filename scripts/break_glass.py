"""
Break-glass session-start gate (Memory Spine Phase 3 — AC3).

The Homebase reconciler (`reconcile.py`) writes the *authoritative* red/green
verdict to `/root/hestia/beacons/claude-memory-reconciler.json`.  The laptop
NEVER computes its own verdict (spec §4.2.5) — it only reads Homebase's.

This module is the laptop-side gate that:
  (a) fetches the authoritative verdict over ssh (short timeout) and keeps a
      local last-known cache so a briefly-unreachable VPS never traps Mr.TL;
  (b) decides block / warn / allow;
  (c) consumes a break-glass token — requires a non-empty reason, writes an
      append-only audit record, and carries a short (24h) TTL;
  (d) NEVER clears the red.  Break-glass only grants a time-boxed bypass; the
      state stays RED until the verdict itself goes green.

Design rule (spec §4.2 pt 7, AC3): a real wall when RED *and reachable*, but the
bypass is one explicit command with a reason — not one-keystroke, not lockout.

Graceful degradation (spec §4.2.6, two liveness contracts): if Homebase is
UNREACHABLE, do NOT hard-block.  Laptop-off / ssh-timeout != stage-dead.

CLI:
    python scripts/break_glass.py --status
    python scripts/break_glass.py --break-glass --reason "shipping hotfix, fix after"

Stdlib only.
"""

import argparse
import json
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# Authoritative verdict lives on Homebase; the reconciler writes it there.
VERDICT_REMOTE = "/root/hestia/beacons/claude-memory-reconciler.json"
SSH_HOST = "homebase"

# Local runtime state (all gitignored — regenerated at runtime).
VERDICT_CACHE = SCRIPTS_DIR / "verdict-cache.json"          # last-known verdict
AUDIT_LOG = SCRIPTS_DIR / "break-glass-audit.jsonl"         # append-only audit
TOKEN_FILE = SCRIPTS_DIR / "break-glass-token.json"         # live bypass token

TTL_HOURS = 24  # short TTL — one working day; re-justify after that


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _machine() -> str:
    return socket.gethostname()


def fetch_verdict(verdict_remote: str = VERDICT_REMOTE):
    """Fetch the authoritative verdict from Homebase over ssh.

    Returns (verdict_dict_or_None, source) where source is one of:
      "remote"      — fresh verdict pulled from Homebase (cache refreshed)
      "cache"       — Homebase unreachable; last-known cached verdict returned
      "unreachable" — Homebase unreachable AND no cache available

    Never raises — a gate must degrade, not crash.
    """
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             SSH_HOST, f"cat {verdict_remote}"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        if result.returncode == 0 and result.stdout.strip():
            verdict = json.loads(result.stdout)
            _write_cache(verdict)
            return verdict, "remote"
    except Exception:
        pass  # fall through to cache

    cached = _read_cache()
    if cached is not None:
        return cached, "cache"
    return None, "unreachable"


def _write_cache(verdict: dict) -> None:
    try:
        VERDICT_CACHE.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    except OSError:
        pass  # cache is best-effort


def _read_cache():
    try:
        return json.loads(VERDICT_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def live_token():
    """Return the live (non-expired) break-glass token dict, or None.

    A token is live iff it exists and its ttl_expires is in the future.  Break-
    glass never clears red — the token only grants a time-boxed bypass.
    """
    try:
        token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        expires = datetime.fromisoformat(token["ttl_expires"])
    except (KeyError, ValueError):
        return None
    if expires <= _now_utc():
        return None  # expired
    return token


def consume_break_glass(reason: str, verdict_remote: str = VERDICT_REMOTE):
    """Consume a break-glass token: validate reason, write audit + token, set TTL.

    Returns (ok, message).  Does NOT clear the red — it only grants a bypass.
    """
    reason = (reason or "").strip()
    if not reason:
        return False, "break-glass requires a non-empty --reason string"

    verdict, source = fetch_verdict(verdict_remote)
    status = (verdict or {}).get("reconcile_status", "unknown")
    failing = (verdict or {}).get("failing", [])

    now = _now_utc()
    expires = now + timedelta(hours=TTL_HOURS)

    audit_row = {
        "ts": now.isoformat(),
        "machine": _machine(),
        "verdict_status": status,
        "verdict_source": source,
        "failing_checks": failing,
        "reason": reason,
        "ttl_expires": expires.isoformat(),
    }

    # Append-only audit log — the bypass becomes evidence.
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_row, separators=(",", ":")) + "\n")
    except OSError as e:
        return False, f"failed to write audit log: {e}"

    # Token grants the time-boxed bypass.
    token = {
        "ts": now.isoformat(),
        "machine": _machine(),
        "reason": reason,
        "ttl_expires": expires.isoformat(),
    }
    try:
        TOKEN_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")
    except OSError as e:
        return False, f"failed to write token: {e}"

    return True, (
        f"break-glass granted until {expires.isoformat()} (TTL {TTL_HOURS}h). "
        f"Verdict is still {status.upper()} — fix it; this bypass is audited."
    )


def gate_decision(verdict_remote: str = VERDICT_REMOTE) -> dict:
    """Decide block / warn / allow for session start.

    Wall rule: BLOCK only when RED *and reachable* with no live token.
      - reachable + green                 -> allow
      - reachable + red + live token      -> warn  (operating under break-glass)
      - reachable + red + no token        -> block (loud; show break-glass cmd)
      - unreachable (cache or none)       -> warn-allow (laptop-off != stage-dead)

    Returns a dict: {decision, status, source, failing, token, message}.
    """
    verdict, source = fetch_verdict(verdict_remote)
    token = live_token()

    if source == "unreachable":
        return {
            "decision": "warn",
            "status": "unknown",
            "source": source,
            "failing": [],
            "token": token,
            "message": ("Memory Spine: Homebase unreachable and no cached verdict. "
                        "Proceeding (laptop-off is not stage-death) — verify the "
                        "reconciler when back online."),
        }

    status = (verdict or {}).get("reconcile_status", "unknown")
    failing = (verdict or {}).get("failing", [])

    # Unreachable-but-cached: never hard-block on a stale verdict we can't confirm.
    if source == "cache":
        stale = (verdict or {}).get("last_run", "unknown")
        note = ""
        if status == "red":
            note = (" Last-known verdict was RED — confirm the reconciler when "
                    "Homebase is reachable.")
        return {
            "decision": "warn",
            "status": status,
            "source": source,
            "failing": failing,
            "token": token,
            "message": (f"Memory Spine: Homebase unreachable; using cached verdict "
                        f"({status.upper()}, as of {stale}). Proceeding.{note}"),
        }

    # source == "remote": authoritative.
    if status != "red":
        return {
            "decision": "allow",
            "status": status,
            "source": source,
            "failing": failing,
            "token": token,
            "message": f"Memory Spine: reconciler {status.upper()}.",
        }

    # RED + reachable.
    if token is not None:
        return {
            "decision": "warn",
            "status": status,
            "source": source,
            "failing": failing,
            "token": token,
            "message": (f"Memory Spine: operating under BREAK-GLASS (expires "
                        f"{token['ttl_expires']}). Reconciler is still RED — fix it."),
        }

    return {
        "decision": "block",
        "status": status,
        "source": source,
        "failing": failing,
        "token": None,
        "message": "Memory Spine reconciler is RED — session-start blocked.",
    }


def _format_failing(failing: list) -> str:
    if not failing:
        return "  (no detail in verdict)"
    lines = []
    for f in failing:
        row = f.get("row", "?")
        check = f.get("check", "?")
        detail = f.get("detail", "")
        lines.append(f"  - {row} ({check}): {detail}")
    return "\n".join(lines)


def render_block_message(decision: dict) -> str:
    """Loud, blocking message: failing checks + the exact break-glass command."""
    return (
        "\n"
        "================ MEMORY SPINE: SESSION BLOCKED (RED) ================\n"
        "The Homebase reconciler reports RED. A live stage is broken:\n"
        f"{_format_failing(decision['failing'])}\n\n"
        "Fix the reconciler (it stays RED until actually fixed), OR break-glass\n"
        "to work now with an audited, time-boxed bypass:\n\n"
        '    python scripts/break_glass.py --break-glass --reason "<why you must proceed>"\n\n'
        "Break-glass requires a reason, is logged, expires in 24h, and does NOT\n"
        "clear the red.\n"
        "====================================================================\n"
    )


def _cmd_status(args) -> int:
    decision = gate_decision(args.verdict_remote)
    token = decision["token"]
    print(f"verdict     : {decision['status'].upper()}")
    print(f"source      : {decision['source']}")
    print(f"decision    : {decision['decision'].upper()}")
    if decision["failing"]:
        print("failing     :")
        print(_format_failing(decision["failing"]))
    if token is not None:
        print(f"break-glass : LIVE (expires {token['ttl_expires']}, reason: {token.get('reason','')!r})")
    else:
        print("break-glass : none")
    print(f"\n{decision['message']}")
    if decision["decision"] == "block":
        print(render_block_message(decision))
        return 1
    return 0


def _cmd_break_glass(args) -> int:
    ok, msg = consume_break_glass(args.reason, args.verdict_remote)
    print(msg)
    return 0 if ok else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true",
                    help="print the authoritative verdict + gate decision")
    ap.add_argument("--break-glass", action="store_true",
                    help="consume a break-glass token (requires --reason)")
    ap.add_argument("--reason", default="",
                    help="why you must proceed under RED (required for --break-glass)")
    ap.add_argument("--verdict-remote", default=VERDICT_REMOTE,
                    help="override the remote verdict path (testing)")
    args = ap.parse_args(argv)

    if args.break_glass:
        return _cmd_break_glass(args)
    # default + --status both print status
    return _cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
