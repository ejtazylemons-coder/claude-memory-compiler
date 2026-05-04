"""
Monthly State-of-the-State synthesis — produces ONE markdown file capturing
who Mr.TL is this month, what changed, what decisions panned out, and the
working partnership notes.

Per scope at: C:/Obsidian/Second Brain/Claude/Notes/State/PROPOSAL.md

Output: C:/Obsidian/Second Brain/Claude/Notes/State/YYYY-MM.md (prior month)

Usage:
    uv run python scripts/monthly-state-synthesis.py            # normal run
    uv run python scripts/monthly-state-synthesis.py --dry-run  # preview, no API
    uv run python scripts/monthly-state-synthesis.py --month 2026-04   # specific month
    uv run python scripts/monthly-state-synthesis.py --force    # overwrite if exists
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from config import KB_ROOT, SCRIPTS_DIR

STATE_DIR = Path("C:/Obsidian/Second Brain/Claude/Notes/State")
HANDOFF_DIR = Path("C:/Obsidian/Second Brain/Claude/Handoff")
DAILY_DIR = KB_ROOT / "daily"
MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Dev-workspace" / "memory"
DECISION_LOG = Path("C:/Dev/pantheon/decisions/decisions.csv")

BUDGET_FILE = SCRIPTS_DIR / "monthly-state-budget.json"
PER_RUN_CAP_USD = 0.50
ANNUAL_CAP_USD = 8.00


def load_budget() -> dict:
    if not BUDGET_FILE.exists():
        return {"year": "", "spent": 0.0, "runs": []}
    return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))


def save_budget(b: dict) -> None:
    BUDGET_FILE.write_text(json.dumps(b, indent=2), encoding="utf-8")


def check_annual_budget(budget: dict) -> float:
    now_year = str(date.today().year)
    if budget.get("year") != now_year:
        budget["year"] = now_year
        budget["spent"] = 0.0
        budget["runs"] = []
        save_budget(budget)
    return ANNUAL_CAP_USD - budget.get("spent", 0.0)


def prior_month_label() -> str:
    """Return YYYY-MM for the month before today."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_of_prior = first_of_this_month - timedelta(days=1)
    return last_of_prior.strftime("%Y-%m")


def month_bounds(month_label: str) -> tuple[date, date]:
    """Given YYYY-MM, return (first_day, last_day) inclusive."""
    year, mo = map(int, month_label.split("-"))
    first = date(year, mo, 1)
    next_mo = date(year + (mo // 12), (mo % 12) + 1, 1)
    last = next_mo - timedelta(days=1)
    return first, last


def collect_daily_logs(start: date, end: date) -> list[Path]:
    logs = []
    if not DAILY_DIR.exists():
        return logs
    for p in sorted(DAILY_DIR.glob("*.md")):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= d <= end:
            logs.append(p)
    return logs


def collect_memory_files(start: date, end: date) -> list[Path]:
    """Memory files modified within the month window."""
    if not MEMORY_DIR.exists():
        return []
    files = []
    start_ts = datetime.combine(start, datetime.min.time()).timestamp()
    end_ts = datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp()
    for p in sorted(MEMORY_DIR.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if start_ts <= mtime < end_ts:
            files.append(p)
    return files


def collect_decisions(start: date, end: date) -> list[dict]:
    if not DECISION_LOG.exists():
        return []
    rows = []
    with DECISION_LOG.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = datetime.strptime(row.get("date", ""), "%Y-%m-%d").date()
            except ValueError:
                continue
            if start <= d <= end:
                rows.append(row)
    return rows


def collect_recent_handoffs(limit: int = 5) -> list[Path]:
    if not HANDOFF_DIR.exists():
        return []
    files = sorted(
        [p for p in HANDOFF_DIR.glob("*.md") if not p.name.startswith("Bootstrap")],
        key=lambda p: p.name,
        reverse=True,
    )
    return files[:limit]


def prior_state_file(month_label: str) -> Path | None:
    year, mo = map(int, month_label.split("-"))
    prior_mo = mo - 1 if mo > 1 else 12
    prior_year = year if mo > 1 else year - 1
    candidate = STATE_DIR / f"{prior_year:04d}-{prior_mo:02d}.md"
    return candidate if candidate.exists() else None


def truncate(text: str, max_chars: int, label: str) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n...(truncated {label} for budget)"


def assemble_input_blob(month_label: str) -> tuple[str, dict]:
    start, end = month_bounds(month_label)

    daily_logs = collect_daily_logs(start, end)
    memory_files = collect_memory_files(start, end)
    decisions = collect_decisions(start, end)
    handoffs = collect_recent_handoffs()
    prior_state = prior_state_file(month_label)

    sections = []

    if prior_state:
        body = prior_state.read_text(encoding="utf-8")
        sections.append(f"## Prior month's state file: {prior_state.name}\n\n{truncate(body, 20000, 'prior state')}")

    if daily_logs:
        combined = []
        for log in daily_logs:
            combined.append(f"### {log.stem}\n\n{log.read_text(encoding='utf-8')}")
        body = "\n\n---\n\n".join(combined)
        sections.append(f"## Daily logs for {month_label}\n\n{truncate(body, 200000, 'daily logs')}")

    if memory_files:
        combined = []
        for m in memory_files:
            try:
                combined.append(f"### {m.name}\n\n{m.read_text(encoding='utf-8')}")
            except OSError:
                continue
        body = "\n\n---\n\n".join(combined)
        sections.append(f"## Memory files modified in {month_label}\n\n{truncate(body, 80000, 'memory files')}")

    if decisions:
        lines = ["| Date | Decision | Reasoning | Expected | Status |", "|---|---|---|---|---|"]
        for r in decisions:
            row = "| " + " | ".join(
                (r.get(k, "") or "").replace("|", "/").replace("\n", " ")[:200]
                for k in ("date", "decision", "reasoning", "expected_outcome", "status")
            ) + " |"
            lines.append(row)
        sections.append(f"## Decisions logged in {month_label}\n\n" + "\n".join(lines))

    if handoffs:
        combined = []
        for h in handoffs:
            try:
                combined.append(f"### {h.name}\n\n{h.read_text(encoding='utf-8')}")
            except OSError:
                continue
        body = "\n\n---\n\n".join(combined)
        sections.append(f"## Recent handoff notes (last {len(handoffs)})\n\n{truncate(body, 40000, 'handoffs')}")

    counts = {
        "daily_logs": len(daily_logs),
        "memory_files": len(memory_files),
        "decisions": len(decisions),
        "handoffs": len(handoffs),
        "prior_state_file": str(prior_state) if prior_state else None,
    }

    return "\n\n---\n\n".join(sections), counts


def build_prompt(month_label: str, input_blob: str, output_path: Path) -> str:
    return f"""You are writing the monthly State-of-the-State file for {month_label} — a candid working model of Mr.TL.

This is NOT a status report. It is YOUR (Claude's) honest synthesis of who Mr.TL is right now,
what changed, what decisions worked, and what's true about the partnership between you. Mr.TL
will read this. He values candor over flattery and has explicitly said no sanitization.

## Inputs (one month of data)

{input_blob}

## Your task

Write ONE markdown file to: {output_path}

## Required structure

```markdown
# {month_label} — State of Mr.TL

## Who Mr.TL is this month
(One paragraph. The working model: personality, current mood, what he's optimizing for, what he's
allergic to, anti-patterns he's working on. Concrete, not generic. If he was a coworker, what would
you tell a new teammate about him?)

## What changed since last month
(Diff against the prior month's State file if one exists. New preferences, retired ones, evolving
taste, priority shifts. If no prior file, write "first State file — baseline." 4-8 bullets.)

## Decisions that landed (or didn't)
(For each medium+ stakes decision logged this month: did it pan out? What did we learn? If a
decision is too recent to evaluate, say so explicitly. 3-6 bullets.)

## Working partnership notes
(What's working between us, what's friction, what you'd flag if you were doing a quarterly check-in.
Honest, not diplomatic. 4-6 bullets.)

## Things to watch
(Anti-patterns you're seeing in him *or* in yourself. Things you'd push back on next month if you
saw them again. If you're not flagging anything, you're hedging — find at least 2.)
```

## Rules

- Length: 800 to 1500 words total. No padding.
- Concrete > abstract. Cite specific events, dates, decisions, file names where possible.
- Use Mr.TL's name, not "the user."
- Plain language. No jargon. No corporate-speak.
- Write ONE file. Do not edit other files. Do not create other files.
- If the input is thin (no daily logs, no decisions), say so honestly and write a shorter file.
"""


async def run_synthesis(prompt: str, output_path: Path, remaining_budget: float) -> float:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    cap = min(PER_RUN_CAP_USD, remaining_budget)
    cost = 0.0
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(SCRIPTS_DIR.parent),
                model="claude-sonnet-4-6",
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
    parser.add_argument("--month", help="YYYY-MM (default: prior month)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output file")
    args = parser.parse_args()

    month_label = args.month or prior_month_label()
    try:
        month_bounds(month_label)
    except (ValueError, IndexError):
        print(f"Invalid --month: {month_label}. Expected YYYY-MM.")
        sys.exit(2)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = STATE_DIR / f"{month_label}.md"

    if output_path.exists() and not args.force:
        print(f"Output already exists: {output_path}")
        print("Skipping. Use --force to overwrite.")
        sys.exit(0)

    print(f"Monthly state synthesis for {month_label}")
    print(f"  Output: {output_path}")

    input_blob, counts = assemble_input_blob(month_label)
    print(f"  Inputs: {counts}")
    print(f"  Input size: {len(input_blob):,} chars")

    if not input_blob.strip():
        print("  No inputs found for this month. Aborting.")
        sys.exit(1)

    prompt = build_prompt(month_label, input_blob, output_path)

    if args.dry_run:
        print("[dry run] exiting without API call")
        return

    budget = load_budget()
    remaining = check_annual_budget(budget)
    print(f"  Year: {budget['year']}  Spent: ${budget['spent']:.2f}  Remaining: ${remaining:.2f}")

    if remaining <= 0.05:
        print("Annual budget exhausted. Skipping.")
        sys.exit(0)

    cost = asyncio.run(run_synthesis(prompt, output_path, remaining))

    budget["spent"] = round(budget.get("spent", 0.0) + cost, 4)
    budget.setdefault("runs", []).append(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "month": month_label,
            "cost": cost,
            "output": str(output_path),
        }
    )
    save_budget(budget)

    print(f"\nDone. Year spent: ${budget['spent']:.2f} / ${ANNUAL_CAP_USD}")

    if not output_path.exists():
        print("WARNING: output file was not written. Check Claude Agent SDK error above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
