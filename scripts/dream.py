#!/usr/bin/env python3
"""dream.py — a local "dreaming" pass over the ~/.claude memory store.

Anthropic's Claude Managed Agents shipped a "Dreaming" feature: an async job that
reviews a memory store + past sessions, fact-checks, dedupes, prunes stale entries,
and rebuilds an index — non-destructively, with a diff a human reviews at the end.

We already hand-rolled the memory store (MEMORY.md + 200+ feedback_*.md files) but
never built the dreaming half. So this store grows unbounded: MEMORY.md is over its
load limit and stuffed with dead "RETIRED/MOOT/killed" lines. This script is the
local dreaming pass — stdlib only, zero LLM cost, NON-DESTRUCTIVE.

It never edits MEMORY.md. It emits a review report (the "diff") so a human (or the
main lights-out terminal) decides what to prune. Observability before features:
the win is *seeing* what the store is carrying, every run.

Checks:
  SIZE        MEMORY.md bytes vs budget; index lines over the per-line char cap.
  BROKEN      index links pointing to memory files that don't exist.
  ORPHAN      memory files on disk that nothing in MEMORY.md references.
  EXPIRED     entries whose own text says they've expired -> genuine prune.
  TOMBSTONED  RETIRED/MOOT/killed lines, split into "explicitly retained (leave)"
              vs "no retention rationale (review)".
  MIRROR      live memory files missing from the git-tracked workspace mirror.

Usage:
  python dream.py                 # run all checks, print summary, save report
  python dream.py --quiet         # report file only, no console body
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
LIVE = Path.home() / ".claude" / "projects" / "C--Dev-workspace" / "memory"
MIRROR = Path("C:/Dev/workspace/claude-config/memory")
INDEX = LIVE / "MEMORY.md"
REPORTS = Path(__file__).resolve().parent.parent / "reports"

# ── Tunables ─────────────────────────────────────────────────────────────
BYTE_BUDGET = 24_400          # the load limit the harness warns at
LINE_CHAR_CAP = 200           # "keep index entries to one line under ~200 chars"

TOMBSTONE = re.compile(
    r"\b(RETIRED|MOOT|KILLED|killed|DEPRECATED|deprecated|DECOMMISSIONED|"
    r"decommissioned|SUPERSEDED|superseded|ARCHIVED|archived|retired|OBSOLETE)\b"
)
# Phrases that mean a dead entry is being kept ON PURPOSE -> do NOT flag for prune.
RETAINED = re.compile(
    r"(kept for|trail kept|for history|for audit|for the record|do not edit|"
    r"obituaries|lessons|retained for|keep for)",
    re.IGNORECASE,
)
# Phrases that mean the entry has a real end-of-life -> genuine prune candidate.
EXPIRED = re.compile(
    r"(expires? (after|on|once)|expired|moot\b|no longer (built|being built|"
    r"valid|relevant)|not being built|do not surface)",
    re.IGNORECASE,
)
LINK = re.compile(r"\[[^\]]+\]\(([A-Za-z0-9_./-]+\.md)\)")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def check_size(index_text: str) -> dict:
    raw = index_text.encode("utf-8")
    lines = index_text.splitlines()
    long_lines = [
        (i + 1, len(ln), ln)
        for i, ln in enumerate(lines)
        if len(ln) > LINE_CHAR_CAP and ln.lstrip().startswith(("-", "*"))
    ]
    reclaimable = sum(n - LINE_CHAR_CAP for _, n, _ in long_lines)
    return {
        "bytes": len(raw),
        "over_budget": len(raw) - BYTE_BUDGET,
        "long_lines": long_lines,
        "reclaimable": reclaimable,
    }


def check_links(index_text: str) -> list[str]:
    broken = []
    for m in LINK.finditer(index_text):
        target = m.group(1)
        if target.startswith(("http", "C:", "/")) or "/" in target:
            continue  # external / absolute / wiki path, not a local memory file
        if not (LIVE / target).exists():
            broken.append(target)
    return sorted(set(broken))


def check_orphans(index_text: str) -> list[str]:
    referenced = set(LINK.findall(index_text))
    orphans = []
    for f in sorted(LIVE.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        # referenced if its filename appears anywhere in the index text
        if f.name not in referenced and f.name not in index_text:
            orphans.append(f.name)
    return orphans


def classify_dead(index_text: str) -> tuple[list, list]:
    """Walk index list-lines; bucket tombstoned ones into retained vs review."""
    expired, review = [], []
    for i, ln in enumerate(index_text.splitlines(), 1):
        s = ln.strip()
        if not s.startswith(("-", "*")):
            continue
        if not TOMBSTONE.search(s):
            continue
        marker = TOMBSTONE.search(s).group(0)
        snippet = (s[:140] + "...") if len(s) > 143 else s
        if EXPIRED.search(s) and not RETAINED.search(s):
            expired.append((i, marker, snippet))
        elif not RETAINED.search(s):
            review.append((i, marker, snippet))
        # RETAINED -> intentional, leave it alone
    return expired, review


def check_mirror() -> list[str]:
    if not MIRROR.exists():
        return ["(mirror dir missing entirely)"]
    missing = []
    for f in sorted(LIVE.glob("*.md")):
        mf = MIRROR / f.name
        if not mf.exists():
            missing.append(f"{f.name} (absent from mirror)")
        elif _read(mf) != _read(f):
            missing.append(f"{f.name} (differs from mirror)")
    return missing


def build_report(size, broken, orphans, expired, review, mirror) -> str:
    L = [f"# Memory Dream — {today()}", ""]
    pct = size["bytes"] / BYTE_BUDGET * 100
    L += [
        "## SIZE",
        f"- MEMORY.md: **{size['bytes']:,} bytes** / {BYTE_BUDGET:,} budget "
        f"({pct:.0f}%) — **{size['over_budget']:+,}** over",
        f"- Index lines over {LINE_CHAR_CAP} chars: **{len(size['long_lines'])}** "
        f"(~{size['reclaimable']:,} bytes reclaimable by trimming to cap)",
        "",
    ]
    if size["long_lines"]:
        L.append("<details><summary>over-long index lines</summary>\n")
        for n, ln, text in size["long_lines"]:
            L.append(f"- L{n} ({ln} chars): {text[:120]}...")
        L.append("\n</details>\n")

    def section(title, items, render):
        L.append(f"## {title} — {len(items)}")
        if not items:
            L.append("- clean\n")
            return
        for it in items:
            L.append(f"- {render(it)}")
        L.append("")

    section("BROKEN LINKS (index → missing file)", broken, lambda x: f"`{x}`")
    section("ORPHAN FILES (on disk, unreferenced)", orphans, lambda x: f"`{x}`")
    section("EXPIRED (own text says end-of-life → prune)", expired,
            lambda x: f"L{x[0]} [{x[1]}] {x[2]}")
    section("TOMBSTONED — no retention rationale (review)", review,
            lambda x: f"L{x[0]} [{x[1]}] {x[2]}")
    section("MIRROR DRIFT (live not in git mirror)", mirror, lambda x: f"`{x}`")

    L += [
        "---",
        "_Non-destructive. Nothing was edited. Prune decisions are the human's._",
        "_Entries marked 'kept for history / audit / lessons' were intentionally "
        "left untouched._",
    ]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Local dreaming pass over the memory store.")
    ap.add_argument("--quiet", action="store_true", help="write report only, no console body")
    args = ap.parse_args()

    if not INDEX.exists():
        print(f"MEMORY.md not found at {INDEX}")
        return 1

    index_text = _read(INDEX)
    size = check_size(index_text)
    broken = check_links(index_text)
    orphans = check_orphans(index_text)
    expired, review = classify_dead(index_text)
    mirror = check_mirror()

    report = build_report(size, broken, orphans, expired, review, mirror)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"memory-dream-{today()}.md"
    out.write_text(report, encoding="utf-8")

    over = size["over_budget"]
    flag = "OVER" if over > 0 else "ok"
    print(f"[dream] MEMORY.md {size['bytes']:,}b ({flag} by {over:+,}) | "
          f"{len(size['long_lines'])} long lines | {len(broken)} broken | "
          f"{len(orphans)} orphan | {len(expired)} expired | "
          f"{len(review)} review | {len(mirror)} mirror-drift")
    print(f"[dream] report -> {out}")
    if not args.quiet:
        print("\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
