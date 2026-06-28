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
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles default to cp1252; the memory store is UTF-8 (arrows, em-dashes,
# emoji). Make console output encoding-robust so a non-cp1252 char in MEMORY.md can
# never crash the auditor. The report file is always written UTF-8 regardless.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

# ── Paths ────────────────────────────────────────────────────────────────
LIVE = Path.home() / ".claude" / "projects" / "C--Dev-workspace" / "memory"
# Mirror is nested per workspace-slug, NOT flat. The flat claude-config/memory/*.md
# is a stale pre-slug layout (106 files) — comparing against it gives false drift.
MIRROR = Path("C:/Dev/workspace/claude-config/memory/C--Dev-workspace")
INDEX = LIVE / "MEMORY.md"
REPORTS = Path(__file__).resolve().parent.parent / "reports"
BEACON_LOCAL = Path(__file__).resolve().parent / "memory-dream.beacon.json"
BEACON_REMOTE = "/root/hestia/beacons/claude-memory-dream.json"

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


def _push_beacon(payload: dict) -> None:
    """Write local beacon + push to Homebase. Best-effort; never changes exit code."""
    try:
        BEACON_LOCAL.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        body = json.dumps(payload, separators=(",", ":"))
        proc = subprocess.Popen(
            ["ssh", "homebase", f"cat > {BEACON_REMOTE}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc.communicate(input=body.encode("utf-8"), timeout=20)
    except Exception as e:
        print(f"[dream] beacon push failed (non-fatal): {type(e).__name__}: {e}")


def classify_exit(size, broken, mirror, review) -> tuple[int, str]:
    """0=clean, 1=integrity fail (broken links / mirror drift), 2=grooming warn."""
    if broken or mirror:
        return 1, f"INTEGRITY: {len(broken)} broken links, {len(mirror)} mirror-drift"
    if size["over_budget"] > 0 or review:
        parts = []
        if size["over_budget"] > 0:
            parts.append(f"MEMORY.md {size['over_budget']:+,}b over budget")
        if review:
            parts.append(f"{len(review)} tombstones to review")
        return 2, "GROOM: " + ", ".join(parts)
    return 0, "clean"


def main():
    ap = argparse.ArgumentParser(description="Local dreaming pass over the memory store.")
    ap.add_argument("--quiet", action="store_true", help="write report only, no console body")
    ap.add_argument("--beacon", action="store_true", help="push health beacon to Homebase (cron use)")
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

    exit_code, summary = classify_exit(size, broken, mirror, review)

    over = size["over_budget"]
    flag = "OVER" if over > 0 else "ok"
    print(f"[dream] MEMORY.md {size['bytes']:,}b ({flag} by {over:+,}) | "
          f"{len(size['long_lines'])} long lines | {len(broken)} broken | "
          f"{len(orphans)} orphan | {len(expired)} expired | "
          f"{len(review)} review | {len(mirror)} mirror-drift")
    print(f"[dream] exit={exit_code} {summary}")
    print(f"[dream] report -> {out}")

    if args.beacon:
        _push_beacon({
            "name": "ClaudeMemoryDream",
            "machine": socket.gethostname(),
            "last_run": now_iso(),
            "exit_code": exit_code,
            "summary": summary[:240],
            "bytes": size["bytes"],
            "over_budget": over,
            "long_lines": len(size["long_lines"]),
            "orphans": len(orphans),
            "review": len(review),
            "broken": len(broken),
            "mirror_drift": len(mirror),
        })

    if not args.quiet:
        print("\n" + report)
    # Exit non-zero only signals the Ops beacon consumer; for an interactive/
    # lights-out run we don't want to abort the surrounding script, so the
    # caller should ignore the code unless --beacon was passed.
    return exit_code if args.beacon else 0


if __name__ == "__main__":
    raise SystemExit(main())
