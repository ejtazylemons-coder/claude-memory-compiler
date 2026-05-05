"""
Weekly rollup — reads last 7 days of daily logs, produces ONE short summary.

Replaces the old auto-compile pipeline (which cost ~$0.80/day on Opus).
Runs on a schedule, once per week. Capped at $0.45 per run — with a
monthly ledger that bails if the month total would exceed $2.00.

Output: C:\\Obsidian\\Second Brain\\Claude\\Knowledge\\weekly\\YYYY-WW.md

Usage:
    uv run python scripts/weekly-rollup.py            # normal run
    uv run python scripts/weekly-rollup.py --dry-run  # preview without API call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Defensive: force UTF-8 stdout regardless of how invoked. The bat sets
# PYTHONIOENCODING=utf-8 but a manual run from cp1252 PowerShell would crash
# at line 182's `→` print otherwise. The 2026-04-19 silent-failure bug class.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from config import DAILY_DIR, KB_ROOT, SCRIPTS_DIR

WEEKLY_DIR = KB_ROOT / "weekly"
BUDGET_FILE = SCRIPTS_DIR / "weekly-budget.json"

# Hard caps — change here if you want to adjust
PER_RUN_CAP_USD = 0.45
MONTHLY_CAP_USD = 2.00


def load_budget() -> dict:
    if not BUDGET_FILE.exists():
        return {"month": "", "spent": 0.0, "runs": []}
    return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))


def save_budget(b: dict) -> None:
    BUDGET_FILE.write_text(json.dumps(b, indent=2), encoding="utf-8")


def current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def check_monthly_budget(budget: dict) -> float:
    """Return USD remaining this month. Reset if new month."""
    now_month = current_month()
    if budget.get("month") != now_month:
        budget["month"] = now_month
        budget["spent"] = 0.0
        budget["runs"] = []
        save_budget(budget)
    return MONTHLY_CAP_USD - budget.get("spent", 0.0)


def last_7_days_logs() -> list[Path]:
    cutoff = date.today() - timedelta(days=7)
    logs = []
    for p in sorted(DAILY_DIR.glob("*.md")):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= cutoff:
            logs.append(p)
    return logs


async def run_rollup(logs: list[Path], output_path: Path, remaining_budget: float) -> dict:
    """Run the weekly rollup. Returns dict with cost/error/output_exists.

    Caller must check error AND output_exists — a $0 cost with output_exists=False
    is a silent failure that must NOT be counted as a successful run.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # Per-day cap — bundled CC subprocess has a Windows pipe buffer limit
    # somewhere between 15KB and 20KB on the prompt; >20KB returns silently
    # with cost=$0 + no response. Cap each day's content to ~1.7KB so the
    # combined log_body across 8 days fits well under the threshold.
    PER_DAY_CAP = 1700
    combined = []
    for log in logs:
        body = log.read_text(encoding="utf-8")
        if len(body) > PER_DAY_CAP:
            body = body[:PER_DAY_CAP] + f"\n\n...(truncated from {len(body)} chars)"
        combined.append(f"## {log.stem}\n\n{body}")
    log_body = "\n\n---\n\n".join(combined)

    # Final hard cap as belt-and-suspenders.
    PROMPT_HARD_CAP = 15_000
    if len(log_body) > PROMPT_HARD_CAP:
        log_body = log_body[:PROMPT_HARD_CAP] + "\n\n...(truncated)"

    prompt = f"""You are summarizing a week of Claude Code session logs into a short, useful weekly note.

## The last 7 days of daily logs

{log_body}

## Your task

Respond with ONLY the markdown content of the weekly note. Do not call any tools, do not say anything else — just emit the markdown directly.

Format:

# Week of {logs[0].stem} -> {logs[-1].stem}

## Themes
- (3-5 bullets — the big threads of the week, not every small thing)

## Decisions made
- (what was decided, one line each, with date)

## Systems built or changed
- (feature/fix/refactor with 1-line impact)

## Open loops
- (anything left unresolved that matters next week)

## Money spent / saved
- (if cost/budget topics came up, summarize)

Rules:
- Keep the whole file under 80 lines. Terse bullets, no fluff.
- Skip conversations that were just config tweaks or small talk.
- Reference dates (e.g. "2026-04-14") when citing a decision or event.
- Output ONLY the markdown — no explanations, no fenced code blocks around it.
"""

    cap = min(PER_RUN_CAP_USD, remaining_budget)
    cost = 0.0
    # Mirror flush.py's pattern: allowed_tools=[], agent returns markdown as
    # text, Python writes the file. This avoids the bundled CC's Write-tool
    # path-confinement edge cases that caused the 2026-04-19 → 2026-05-04
    # silent-zero failures (SDK reports cost=$0 + no file written).
    error_msg: str | None = None
    response_text = ""
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(SCRIPTS_DIR.parent),
                allowed_tools=[],
                max_turns=2,
                max_budget_usd=cap,
            ),
        ):
            if isinstance(message, ResultMessage):
                cost = message.total_cost_usd or 0.0
                print(f"  Cost: ${cost:.4f}")
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"  Error: {error_msg}")

    # Python writes the file — not the agent. Strip leading/trailing whitespace
    # and any accidental fenced code block wrapping.
    if response_text.strip() and not error_msg:
        clean = response_text.strip()
        if clean.startswith("```"):
            # Strip wrapping fence if model ignored the no-fence rule
            lines = clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(clean + "\n", encoding="utf-8")

    return {"cost": cost, "error": error_msg, "output_exists": output_path.exists()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    logs = last_7_days_logs()
    if not logs:
        print("No daily logs in the last 7 days. Nothing to roll up.")
        return

    iso_year, iso_week, _ = date.today().isocalendar()
    output_path = WEEKLY_DIR / f"{iso_year}-W{iso_week:02d}.md"

    print(f"Weekly rollup for {logs[0].stem} → {logs[-1].stem}")
    print(f"  Logs: {len(logs)}")
    print(f"  Output: {output_path}")
    print(f"  Per-run cap: ${PER_RUN_CAP_USD}  Monthly cap: ${MONTHLY_CAP_USD}")

    if args.dry_run:
        print("[dry run] exiting without API call")
        return

    budget = load_budget()
    remaining = check_monthly_budget(budget)
    print(f"  Month: {budget['month']}  Spent: ${budget['spent']:.2f}  Remaining: ${remaining:.2f}")

    if remaining <= 0.05:
        print("Monthly budget exhausted. Skipping.")
        sys.exit(0)

    result = asyncio.run(run_rollup(logs, output_path, remaining))
    cost = result["cost"]

    # Silent-failure detection: cost reported as $0 AND output not written =
    # SDK/agent silent failure (the 2026-05-03 incident). Refuse to count it
    # as a clean run; surface a non-zero exit so the wrapper sees it.
    if not result["output_exists"]:
        print(f"\nFAIL: rollup returned cost=${cost:.4f} but {output_path.name} was not written")
        if result["error"]:
            print(f"  underlying error: {result['error']}")
        sys.exit(1)

    budget["spent"] = round(budget.get("spent", 0.0) + cost, 4)
    budget.setdefault("runs", []).append(
        {"ts": datetime.now().isoformat(timespec="seconds"), "cost": cost, "output": str(output_path)}
    )
    save_budget(budget)

    print(f"\nDone. Month spent: ${budget['spent']:.2f} / ${MONTHLY_CAP_USD}")


if __name__ == "__main__":
    main()
