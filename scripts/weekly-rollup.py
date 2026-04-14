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


async def run_rollup(logs: list[Path], output_path: Path, remaining_budget: float) -> float:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    combined = []
    for log in logs:
        combined.append(f"## {log.stem}\n\n{log.read_text(encoding='utf-8')}")
    log_body = "\n\n---\n\n".join(combined)

    # Hard truncate to keep prompt size sane (~400K chars ≈ 100K tokens)
    if len(log_body) > 400_000:
        log_body = log_body[:400_000] + "\n\n...(truncated for budget)"

    prompt = f"""You are summarizing a week of Claude Code session logs into a short, useful weekly note.

## The last 7 days of daily logs

{log_body}

## Your task

Write ONE markdown file to: {output_path}

Format:

```markdown
# Week of {logs[0].stem} → {logs[-1].stem}

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
```

Rules:
- Keep the whole file under 80 lines. Terse bullets, no fluff.
- Skip conversations that were just config tweaks or small talk.
- Reference dates (e.g. "2026-04-14") when citing a decision or event.
- Write ONE file. Do not create any other files. Do not edit other files.
"""

    cap = min(PER_RUN_CAP_USD, remaining_budget)
    cost = 0.0
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(SCRIPTS_DIR.parent),
                model="claude-sonnet-4-5",
                system_prompt={"type": "preset", "preset": "claude_code"},
                allowed_tools=["Write"],
                permission_mode="acceptEdits",
                max_turns=5,
                max_budget_usd=cap,
            ),
        ):
            if isinstance(message, ResultMessage):
                cost = message.total_cost_usd or 0.0
                print(f"  Cost: ${cost:.4f}")
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        pass
    except Exception as e:
        print(f"  Error: {e}")

    return cost


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

    cost = asyncio.run(run_rollup(logs, output_path, remaining))

    budget["spent"] = round(budget.get("spent", 0.0) + cost, 4)
    budget.setdefault("runs", []).append(
        {"ts": datetime.now().isoformat(timespec="seconds"), "cost": cost, "output": str(output_path)}
    )
    save_budget(budget)

    print(f"\nDone. Month spent: ${budget['spent']:.2f} / ${MONTHLY_CAP_USD}")


if __name__ == "__main__":
    main()
