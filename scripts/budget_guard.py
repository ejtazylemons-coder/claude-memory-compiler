"""Budget + auto-shutoff for the weekly compile pipeline.

Two layers of safety:

1. **Combined monthly cap** across rollup + compile. If total this month would
   exceed `MONTHLY_HARD_CAP_USD`, the next run is blocked AND the compiler
   auto-disables (writes `.compiler-disabled.flag`). Manual unblock required:
   delete the flag.

2. **Silent-zero detector**. If a run reports cost==0 AND the expected output
   was not actually produced, that's a silent failure (the 2026-05-03 incident).
   Two consecutive silent zeros auto-disable.

Auto-disable triggers a Telegram alert via Hermes (TELEGRAM_BOT_TOKEN +
TELEGRAM_CHAT_ID from `.env`). The Ops worker on Homebase also surfaces
the disabled state via beacon `exit_code=2` (warn).

Re-enable: `python scripts/budget_guard.py --enable` (or just delete
`.compiler-disabled.flag`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPTS_DIR.parent
DISABLED_FLAG = SCRIPTS_DIR / ".compiler-disabled.flag"
COMBINED_BUDGET_FILE = SCRIPTS_DIR / "combined-budget.json"
SILENT_ZERO_COUNTER = SCRIPTS_DIR / ".silent-zero-counter.json"

# Hard caps — combined across rollup + compile.
# Sized for steady-state: 7 daily compiles/wk × $0.40 avg + 1 rollup × $0.15
# = ~$3/week × 4 = ~$12/month plus a 25% buffer. Initial run catches up a
# multi-week backlog, throttled to 5 files/run by the orchestrator.
MONTHLY_HARD_CAP_USD = 15.00     # auto-disable above this
MONTHLY_SOFT_WARN_USD = 10.00    # Telegram nudge but keep running
MAX_CONSECUTIVE_SILENT_ZEROS = 2  # auto-disable on 2nd in a row


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_disabled() -> tuple[bool, str]:
    """Return (disabled, reason). reason is empty when not disabled."""
    if not DISABLED_FLAG.exists():
        return False, ""
    try:
        return True, DISABLED_FLAG.read_text(encoding="utf-8").strip() or "(no reason recorded)"
    except OSError as e:
        return True, f"(could not read flag: {e})"


def disable(reason: str) -> None:
    """Write the disable flag. Idempotent."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    body = f"{timestamp} {reason}\n"
    DISABLED_FLAG.write_text(body, encoding="utf-8")


def enable() -> bool:
    """Remove the disable flag. Returns True if it existed."""
    if DISABLED_FLAG.exists():
        DISABLED_FLAG.unlink()
        return True
    return False


def load_combined_budget() -> dict:
    """Single ledger across rollup + compile. Auto-resets on month rollover."""
    budget = _load_json(COMBINED_BUDGET_FILE, {"month": "", "spent": 0.0, "runs": []})
    if budget.get("month") != _current_month():
        budget = {"month": _current_month(), "spent": 0.0, "runs": []}
        _save_json(COMBINED_BUDGET_FILE, budget)
    return budget


def record_run(script_name: str, cost: float, output_files: list[str]) -> dict:
    """Append a run to the combined ledger. Returns the updated budget dict."""
    budget = load_combined_budget()
    budget["spent"] = round(budget.get("spent", 0.0) + cost, 4)
    budget.setdefault("runs", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "script": script_name,
        "cost": cost,
        "outputs": output_files,
    })
    _save_json(COMBINED_BUDGET_FILE, budget)
    return budget


def check_remaining() -> tuple[float, bool, bool]:
    """Return (remaining_usd, hit_soft_warn, hit_hard_cap)."""
    spent = load_combined_budget().get("spent", 0.0)
    remaining = MONTHLY_HARD_CAP_USD - spent
    return remaining, spent >= MONTHLY_SOFT_WARN_USD, spent >= MONTHLY_HARD_CAP_USD


def record_silent_zero() -> int:
    """Increment consecutive-silent-zero counter. Returns new count."""
    data = _load_json(SILENT_ZERO_COUNTER, {"count": 0, "last_ts": None})
    data["count"] = data.get("count", 0) + 1
    data["last_ts"] = datetime.now().isoformat(timespec="seconds")
    _save_json(SILENT_ZERO_COUNTER, data)
    return data["count"]


def reset_silent_zero() -> None:
    _save_json(SILENT_ZERO_COUNTER, {"count": 0, "last_ts": None})


def silent_zero_count() -> int:
    return _load_json(SILENT_ZERO_COUNTER, {"count": 0}).get("count", 0)


def telegram_alert(text: str) -> bool:
    """Send a Telegram alert via the bot token in .env. Best-effort.

    Returns True on success, False on any failure (including missing creds).
    Does not raise — alerts must never block the main flow.
    """
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return False

    token = ""
    chat_id = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
        elif line.startswith("TELEGRAM_CHAT_ID="):
            chat_id = line.split("=", 1)[1].strip()

    if not token or not chat_id:
        return False

    import urllib.parse
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Budget guard for weekly compile pipeline")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--enable", action="store_true", help="Clear the disable flag")
    parser.add_argument("--disable", metavar="REASON", help="Set disable flag with reason (testing)")
    args = parser.parse_args()

    if args.enable:
        if enable():
            print("Compiler enabled. Disable flag removed.")
            reset_silent_zero()
        else:
            print("Already enabled (no flag present).")
        return 0

    if args.disable:
        disable(args.disable)
        print(f"Compiler disabled: {args.disable}")
        return 0

    # Default: --status
    disabled, reason = is_disabled()
    remaining, soft, hard = check_remaining()
    budget = load_combined_budget()
    silent_count = silent_zero_count()
    print(f"Disabled: {disabled}{(' — ' + reason) if disabled else ''}")
    print(f"Month: {budget['month']}  Spent: ${budget['spent']:.4f}  Remaining: ${remaining:.4f}")
    print(f"Hard cap: ${MONTHLY_HARD_CAP_USD}  Soft warn at: ${MONTHLY_SOFT_WARN_USD}")
    print(f"Soft warn hit: {soft}  Hard cap hit: {hard}")
    print(f"Consecutive silent zeros: {silent_count} (auto-disable at {MAX_CONSECUTIVE_SILENT_ZEROS})")
    print(f"Runs this month: {len(budget.get('runs', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
