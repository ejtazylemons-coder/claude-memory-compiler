"""
Compile daily conversation logs into structured knowledge articles.

This is the "LLM compiler" - it reads daily logs (source code) and produces
organized knowledge articles (the executable).

Usage:
    uv run python compile.py                    # compile new/changed logs only
    uv run python compile.py --all              # force recompile everything
    uv run python compile.py --file daily/2026-04-01.md  # compile a specific log
    uv run python compile.py --dry-run          # show what would be compiled
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Defensive: force UTF-8 stdout regardless of how invoked. Same fix class as
# weekly-rollup.py — wiki articles + concept names contain Unicode that cp1252
# can't encode, so any print() of agent output would crash on Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from config import AGENTS_FILE, CONCEPTS_DIR, CONNECTIONS_DIR, DAILY_DIR, KB_ROOT, KNOWLEDGE_DIR, now_iso
from utils import (
    file_hash,
    list_raw_files,
    list_wiki_articles,
    load_state,
    read_wiki_index,
    save_state,
)

# ── Paths for the LLM to use ──────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent


async def compile_daily_log(log_path: Path, state: dict) -> float:
    """Compile a single daily log into knowledge articles.

    Returns the API cost of the compilation.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    log_content = log_path.read_text(encoding="utf-8")
    schema = AGENTS_FILE.read_text(encoding="utf-8")
    wiki_index = read_wiki_index()

    # Count existing articles for context. We deliberately do NOT embed their full
    # text: at ~63 articles that was ~90K tokens (~360KB) in EVERY prompt, which
    # overflowed the CLI's message reader and crashed compilation with "exit code
    # 143" (it grew O(n) with the wiki and broke once the wiki passed ~50 articles).
    # The agent has Read/Glob/Grep + add_dirs=KB_ROOT and the wiki index below, so it
    # reads the specific articles it needs to update/link on demand — constant-size
    # prompt that scales no matter how large the wiki grows.
    existing_article_count = len(list_wiki_articles())

    timestamp = now_iso()

    prompt = f"""You are a knowledge compiler. Your job is to read a daily conversation log
and extract knowledge into structured wiki articles.

## Schema (AGENTS.md)

{schema}

## Current Wiki Index

{wiki_index}

## Existing Wiki Articles

There are {existing_article_count} existing articles — see the **Current Wiki Index** above for the full catalog (titles + summaries). They live in `knowledge/concepts/` and `knowledge/connections/` (inside your working dirs). When you need to UPDATE or LINK to an existing article, use Read/Glob/Grep to inspect that specific file first — do not assume its contents.

## Daily Log to Compile

**File:** {log_path.name}

{log_content}

## Your Task

Read the daily log above and compile it into wiki articles following the schema exactly.

### Rules:

1. **Extract key concepts** - Identify 3-7 distinct concepts worth their own article
2. **Create concept articles** in `knowledge/concepts/` - One .md file per concept
   - Use the exact article format from AGENTS.md (YAML frontmatter + sections)
   - Include `sources:` in frontmatter pointing to the daily log file
   - Use `[[concepts/slug]]` wikilinks to link to related concepts
   - Write in encyclopedia style - neutral, comprehensive
3. **Create connection articles** in `knowledge/connections/` if this log reveals non-obvious
   relationships between 2+ existing concepts
4. **Update existing articles** if this log adds new information to concepts already in the wiki
   - Read the existing article, add the new information, add the source to frontmatter
5. **Update knowledge/index.md** - Add new entries under the correct category section
   - The index is a wiki with category sections (Infrastructure & Monitoring, Security, Protocols & Workflows, Knowledge Systems, BEACON Platform, Connections)
   - Add each new article as a table row under the matching section: `| [[path/slug]] | One-line summary | source-file | {timestamp[:10]} |`
   - If an existing article was updated, update its "Updated" date in its row
   - If no existing section fits the new article, create a new `## Category Name` section with a table
   - Update the article count in the header line (e.g. `> **20 articles**`) after adding entries
   - Update the Table of Contents if a new section was added
6. **Append to knowledge/log.md** - Add a timestamped entry:
   ```
   ## [{timestamp}] compile | {log_path.name}
   - Source: daily/{log_path.name}
   - Articles created: [[concepts/x]], [[concepts/y]]
   - Articles updated: [[concepts/z]] (if any)
   ```

### File paths:
- Write concept articles to: {CONCEPTS_DIR}
- Write connection articles to: {CONNECTIONS_DIR}
- Update index at: {KNOWLEDGE_DIR / 'index.md'}
- Append log at: {KNOWLEDGE_DIR / 'log.md'}

### Quality standards:
- Every article must have complete YAML frontmatter
- Every article must link to at least 2 other articles via [[wikilinks]]
- Key Points section should have 3-5 bullet points
- Details section should have 2+ paragraphs
- Related Concepts section should have 2+ entries
- Sources section should cite the daily log with specific claims extracted
"""

    cost = 0.0
    # Don't swallow exceptions silently — see weekly-rollup.py for the same fix.
    # Caller (run-weekly-compile.py) must distinguish $0 cost from $0 cost + raise.
    error_raised: BaseException | None = None

    # STDERR DRAIN — load-bearing, do not remove. The SDK only pipes the nested
    # CLI's stderr when a `stderr` callback is set (subprocess_cli.py: `should_pipe_stderr
    # = options.stderr is not None`). With NO callback, stderr is left inherited/
    # uncontrolled and this tool-heavy claude_code-preset session DEADLOCKS mid-run
    # (reproduced 2026-07-07: identical prompt+options hung indefinitely → exit 143
    # on kill; adding this drain = clean 45s exit). The bare-text rollup/errorlog
    # queries (allowed_tools=[]) emit too little stderr to hit it, which is why only
    # compile hung. The callback also captures the tail so a future failure is
    # visible instead of silent (the 2026-06-28 "surface hidden stderr" lesson).
    stderr_tail: list[str] = []
    def _drain_stderr(line: str) -> None:
        stderr_tail.append(line)
        if len(stderr_tail) > 50:
            del stderr_tail[0]

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(ROOT_DIR),
                model="claude-sonnet-4-5",
                system_prompt={"type": "preset", "preset": "claude_code"},
                allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
                permission_mode="acceptEdits",
                stderr=_drain_stderr,
                # KB_ROOT (Obsidian vault) is outside cwd; must be explicitly
                # allowed or bundled CC's Write/Edit will crash with exit 1.
                # See weekly-rollup.py for the same fix + history.
                add_dirs=[str(KB_ROOT)],
                max_turns=30,
                # Per-file safety ceiling. A real compile of a content-rich daily
                # log costs ~$0.3–0.6; the old $0.50 cap was BELOW typical cost, so
                # the CLI hit the ceiling (ResultMessage subtype=error_max_budget_usd)
                # and exited 1 mid-task even though articles were written — a second
                # cause of the Phase 0 exit-1 (alongside the hook issue below). Raise
                # to give normal compiles headroom to finish naturally and exit 0.
                # This stays a runaway guard; the orchestrator enforces the monthly
                # spend cap, so a rarely-hit per-file ceiling is the right place for it.
                max_budget_usd=1.50,
                # Settings-source isolation: this nested batch session must NOT
                # load the user's ~/.claude/settings.json NOR the repo's project
                # .claude/settings.json. Both register SessionEnd hooks that fail
                # ("Hook cancelled") in the nested SDK subprocess, making the
                # inner CLI exit 1 even though the compile work succeeded — which
                # left the wiki frozen (Phase 0). Emitting `--setting-sources ""`
                # loads no user/project/local settings, so no hooks fire. Auth
                # comes from credentials, not settings.json, so subscription
                # billing is unaffected.
                extra_args={"setting-sources": ""},
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        pass  # compilation output - LLM writes files directly
            elif isinstance(message, ResultMessage):
                cost = message.total_cost_usd or 0.0
                print(f"  Cost: ${cost:.4f}")
    except Exception as e:
        error_raised = e
        print(f"  Error: {type(e).__name__}: {e}")
        if stderr_tail:
            print("  --- nested CLI stderr (last lines) ---")
            for _l in stderr_tail[-20:]:
                print(f"  | {_l}")

    # Only record state if the compile didn't raise. A bare $0 cost (with no
    # exception) still updates state — the LLM may have legitimately decided
    # there was nothing new to extract for this day.
    if error_raised is None:
        rel_path = log_path.name
        state.setdefault("ingested", {})[rel_path] = {
            "hash": file_hash(log_path),
            "compiled_at": now_iso(),
            "cost_usd": cost,
        }
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        save_state(state)
    else:
        # Re-raise so caller can react. Returning 0.0 silently was the bug.
        raise error_raised

    return cost


def main():
    parser = argparse.ArgumentParser(description="Compile daily logs into knowledge articles")
    parser.add_argument("--all", action="store_true", help="Force recompile all logs")
    parser.add_argument("--file", type=str, help="Compile a specific daily log file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be compiled")
    parser.add_argument("--limit", type=int, default=0, help="Max files to compile this run (0=no limit)")
    args = parser.parse_args()

    state = load_state()

    # Determine which files to compile
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = DAILY_DIR / target.name
        if not target.exists():
            # Try resolving relative to project root
            target = ROOT_DIR / args.file
        if not target.exists():
            print(f"Error: {args.file} not found")
            sys.exit(1)
        to_compile = [target]
    else:
        all_logs = list_raw_files()
        if args.all:
            to_compile = all_logs
        else:
            to_compile = []
            for log_path in all_logs:
                rel = log_path.name
                prev = state.get("ingested", {}).get(rel, {})
                if not prev or prev.get("hash") != file_hash(log_path):
                    to_compile.append(log_path)

    if not to_compile:
        print("Nothing to compile - all daily logs are up to date.")
        return

    if args.limit > 0 and len(to_compile) > args.limit:
        print(f"Backlog has {len(to_compile)} file(s); limiting this run to {args.limit} (per-run safety cap).")
        to_compile = to_compile[: args.limit]

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Files to compile ({len(to_compile)}):")
    for f in to_compile:
        print(f"  - {f.name}")

    if args.dry_run:
        return

    # Compile each file sequentially
    total_cost = 0.0
    for i, log_path in enumerate(to_compile, 1):
        print(f"\n[{i}/{len(to_compile)}] Compiling {log_path.name}...")
        cost = asyncio.run(compile_daily_log(log_path, state))
        total_cost += cost
        print(f"  Done.")

    articles = list_wiki_articles()
    print(f"\nCompilation complete. Total cost: ${total_cost:.2f}")
    print(f"Knowledge base: {len(articles)} articles")


if __name__ == "__main__":
    main()
