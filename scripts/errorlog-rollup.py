"""
ErrorLog rollup — analyzes the Claude Error Log for recurring drift patterns.

Reads C:/Obsidian/Second Brain/Claude/ErrorLog.md (the raw incident log of
Claude drift / rule violations / key leaks), filters to entries from the last
14 days, and asks the LLM to synthesize a weekly report:

- Which rules are being violated repeatedly (≥3x = hook recommendation)
- Which NEW PATTERN entries need new rules created
- Which "shipped" hooks/rules are still being violated (= the fix didn't fix)

Output: C:/Obsidian/Second Brain/Claude/Knowledge/reports/errorlog/YYYY-WW.md

Mirrors weekly-rollup.py — same SDK shape, same budget pattern. Capped at
$0.20/run because the ErrorLog is typically small (entries are short).

Usage:
    uv run python scripts/errorlog-rollup.py            # normal run
    uv run python scripts/errorlog-rollup.py --dry-run  # preview without API call
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from config import KB_ROOT, SCRIPTS_DIR

ERROR_LOG_PATH = Path("C:/Obsidian/Second Brain/Claude/ErrorLog.md")
REPORTS_DIR = KB_ROOT / "reports" / "errorlog"

PER_RUN_CAP_USD = 0.20
LOOKBACK_DAYS = 14
PROMPT_HARD_CAP = 15_000


def extract_recent_entries(log_text: str, days: int) -> tuple[str, int]:
    """Pull entries from the `## Entries` section that fall in the lookback window.

    Returns (entries_text, count). Entries are blocks beginning with `### YYYY-MM-DD HH:MM`.
    """
    # Isolate the Entries section (between ## Entries and the next ## heading)
    m = re.search(
        r"^## Entries\s*\n(.*?)(?=^## |\Z)",
        log_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        return "", 0
    entries_block = m.group(1)

    cutoff = date.today() - timedelta(days=days)
    # Each entry starts with `### YYYY-MM-DD HH:MM — ...`
    entry_pattern = re.compile(
        r"(### (\d{4}-\d{2}-\d{2})[^\n]*\n(?:.(?!^### ))*)",
        flags=re.MULTILINE | re.DOTALL,
    )

    kept = []
    count = 0
    for match in entry_pattern.finditer(entries_block):
        body, date_str = match.group(1), match.group(2)
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= cutoff:
            kept.append(body.rstrip())
            count += 1
    return "\n\n".join(kept), count


async def run_errorlog_rollup(entries_text: str, count: int, output_path: Path) -> dict:
    """Send entries to Claude, get back a synthesis report. Mirrors weekly-rollup.py."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    body = entries_text
    if len(body) > PROMPT_HARD_CAP:
        body = body[:PROMPT_HARD_CAP] + "\n\n...(truncated)"

    prompt = f"""You are analyzing Mr.TL's Claude Error Log to surface recurring drift patterns.

## Context

This log captures times Claude (the AI coding assistant) drifted, violated a stated rule, leaked
something, or otherwise screwed up. Each entry maps the incident to an existing rule file
(`feedback_*.md`) or marks it as "NEW PATTERN — no rule yet".

The log is the INPUT TRAY. The harness already has ~80 distilled rules in feedback_*.md files.
What's missing is awareness of which rules KEEP getting violated despite existing — that means
the rule is sitting in memory but not firing at response time, and needs a stronger enforcement
vehicle (hook, not memory note).

## The last {LOOKBACK_DAYS} days of entries ({count} total)

{body}

## Your task

Respond with ONLY the markdown content of the report. Do NOT call any tools. Just emit markdown.

Format:

# Error Log Report — Week of {date.today().isoformat()}

## Recurring violations (≥3× in {LOOKBACK_DAYS} days)
- **<rule_name>** — N× | Hook recommendation: <specific actionable suggestion, or "rule may need retirement / re-scoping if violations indicate the rule itself is wrong">
- ... (one bullet per rule that hit ≥3×; if none, write "None this week")

## Repeat offenders (2× — watch these)
- **<rule_name>** — 2× | Note: <what to watch for>
- ... (if none, omit section)

## New patterns (entries marked NEW PATTERN)
- **<short pattern name>** — N occurrences | Suggested rule name: `feedback_<slug>.md` | Rationale: <one sentence>
- ... (if none, omit section)

## Hooks that aren't working
- **<rule_name>** — has an enforcement hook but still violated N× this window. Investigate.
- ... (only if any apply; if Claude can infer from entries that a fix was claimed but recurrence continues; otherwise omit section)

## Honest read
<2–4 sentences. What's the throughline? Is the harness working? Where is the leakage? Be direct — Mr.TL would rather read a brutal read than a polite one.>

Rules:
- If the log section is empty or {count} == 0, emit ONLY: "# Error Log Report — Week of {date.today().isoformat()}\\n\\nNo entries this window. Either a clean week or Mr.TL didn't catch drift in real time."
- Keep the whole file under 80 lines. Terse, no fluff.
- Reference rule files by EXACT filename (e.g. `feedback_never_expose_keys.md`).
- Do not invent rules that don't appear in the entries. If an entry says NEW PATTERN, propose a NAME but flag it as proposed, not existing.
- Output ONLY the markdown — no fenced code blocks, no preamble, no "Here is the report:".
"""

    cost = 0.0
    error_msg: str | None = None
    response_text = ""
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(SCRIPTS_DIR.parent),
                allowed_tools=[],
                max_turns=2,
                max_budget_usd=PER_RUN_CAP_USD,
                # Settings-source isolation: don't load the user's or the repo's
                # .claude/settings.json in this nested batch session. Their
                # SessionEnd hooks fail ("Hook cancelled") in the SDK subprocess
                # and can make the inner CLI exit 1. `--setting-sources ""` loads
                # no settings, so no hooks fire. Auth is unaffected (it lives in
                # credentials, not settings.json). Same fix as compile.py.
                extra_args={"setting-sources": ""},
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

    if response_text.strip() and not error_msg:
        clean = response_text.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(clean + "\n", encoding="utf-8")

    return {"cost": cost, "error": error_msg, "output_exists": output_path.exists()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if not ERROR_LOG_PATH.exists():
        print(f"ErrorLog not found at {ERROR_LOG_PATH} — nothing to roll up. (This is fine if the log was never used.)")
        return 0

    log_text = ERROR_LOG_PATH.read_text(encoding="utf-8")
    entries_text, count = extract_recent_entries(log_text, LOOKBACK_DAYS)
    print(f"ErrorLog rollup: {count} entries in last {LOOKBACK_DAYS} days")

    iso_year, iso_week, _ = date.today().isocalendar()
    output_path = REPORTS_DIR / f"{iso_year}-W{iso_week:02d}.md"
    print(f"  Output: {output_path}")

    if count == 0:
        # Write a zero-entry report directly without an API call.
        output_path.write_text(
            f"# Error Log Report — Week of {date.today().isoformat()}\n\n"
            f"No entries in the last {LOOKBACK_DAYS} days. Either a clean window "
            "or Mr.TL didn't catch drift in real time.\n",
            encoding="utf-8",
        )
        print("  Zero entries — wrote empty-week report, no API call.")
        return 0

    if args.dry_run:
        print(f"[dry run] Would synthesize {count} entries into {output_path.name}")
        return 0

    result = asyncio.run(run_errorlog_rollup(entries_text, count, output_path))

    if not result["output_exists"]:
        print(f"FAIL: cost=${result['cost']:.4f} but {output_path.name} was not written")
        if result["error"]:
            print(f"  underlying error: {result['error']}")
        return 1

    print(f"Done. Cost: ${result['cost']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
