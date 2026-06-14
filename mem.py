#!/usr/bin/env python3
"""mem.py — token-free PULL retrieval over the Obsidian knowledge wiki.

Karpathy LLM-Wiki pattern, the pull half: instead of an embedding hook PUSHING
"possibly relevant" notes into every prompt (which scored 0/230 because vectors
score near-random on private jargon), the agent PULLS — it reads a fresh index
of one-line summaries and runs a keyword (BM25) search to open the pages it
actually needs. Pure Python stdlib: no embeddings, no vector DB, no LLM call,
zero tokens.

Usage:
  python mem.py index                 # fresh one-line-summary index of all wiki pages
  python mem.py search "<query>" [-n N] [--daily]
"""
import argparse, glob, math, os, re, sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # wiki + output contain — and → ; cp1252 can't
except Exception:
    pass

WIKI = Path(r"C:\Obsidian\Second Brain\Claude\Knowledge\knowledge")
DAILY = Path(r"C:\Obsidian\Second Brain\Claude\Knowledge\daily")

WORD = re.compile(r"[a-z0-9]+")
FRONT = re.compile(r"^---\s*$")
# Drop common words from the QUERY so rare, meaningful terms drive ranking
# (this is what kept "memory compiler" from winning — "how/does/the/work" diluted it).
STOP = set("a an the of to in on for and or is are was were be do does did how "
           "what why when where which who that this it its with my our your you i "
           "we they he she them me work works working use used using about into".split())


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def title_and_summary(text, fallback):
    """Deterministic one-liner: frontmatter title (or H1/filename) + first real sentence."""
    lines = text.splitlines()
    i = 0
    title = None
    # skip + scan YAML frontmatter for a title:
    if lines and FRONT.match(lines[0]):
        i = 1
        while i < len(lines) and not FRONT.match(lines[i]):
            m = re.match(r"\s*title\s*:\s*(.+?)\s*$", lines[i])
            if m:
                title = m.group(1).strip().strip('"').strip("'")
            i += 1
        i += 1  # past closing ---
    summary = ""
    for ln in lines[i:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            if not title:
                title = s.lstrip("# ").strip()
            continue
        if s.startswith(("|", ">", "-", "*", "```")):
            continue
        summary = s
        break
    if not title:
        title = fallback
    # first sentence, capped
    summary = re.split(r"(?<=[.!?])\s", summary)[0] if summary else ""
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."
    return title, summary


def docs(include_daily=False):
    paths = sorted(glob.glob(str(WIKI / "**" / "*.md"), recursive=True))
    if include_daily:
        paths += sorted(glob.glob(str(DAILY / "*.md")))
    out = []
    for p in paths:
        if Path(p).name.lower() in ("index.md", "log.md"):
            continue  # meta-files, not content pages — they match everything
        text = _read(p)
        if not text.strip():
            continue
        rel = os.path.relpath(p, WIKI.parent)
        out.append((rel, text))
    return out


def tokenize(s):
    return WORD.findall(s.lower())


def bm25(query, corpus, k1=1.5, b=0.75):
    """corpus = list of (id, text). Returns [(score, id), ...] sorted desc."""
    q = set(tokenize(query)) - STOP
    if not q:
        q = set(tokenize(query))  # query was all stopwords — fall back to raw
    if not q:
        return []
    toks = [(cid, tokenize(text)) for cid, text in corpus]
    N = len(toks)
    avgdl = sum(len(t) for _, t in toks) / max(N, 1)
    df = Counter()
    for _, t in toks:
        for w in set(t) & q:
            df[w] += 1
    scored = []
    for cid, t in toks:
        tf = Counter(t)
        dl = len(t)
        score = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
            denom = tf[w] + k1 * (1 - b + b * dl / max(avgdl, 1))
            score += idf * (tf[w] * (k1 + 1)) / denom
        if score > 0:
            scored.append((score, cid))
    scored.sort(reverse=True)
    return scored


def cmd_index(_args):
    rows = []
    for rel, text in docs():
        title, summ = title_and_summary(text, Path(rel).stem)
        rows.append((title, rel.replace("\\", "/"), summ))
    rows.sort(key=lambda r: r[0].lower())
    print(f"# Knowledge Index — {len(rows)} pages (token-free, generated)\n")
    for title, rel, summ in rows:
        print(f"- **{title}** — {summ}  `({rel})`")


def cmd_search(args):
    corpus = [(rel, text) for rel, text in docs(include_daily=args.daily)]
    results = bm25(args.query, corpus)[: args.n]
    if not results:
        print(f'No matches for: {args.query!r}')
        return
    print(f'# PULL results for: {args.query!r}  ({len(results)} of {len(corpus)} pages)\n')
    for score, rel in results:
        text = dict(corpus)[rel]
        title, summ = title_and_summary(text, Path(rel).stem)
        print(f"[{score:5.2f}] **{title}**  `{rel.replace(chr(92),'/')}`")
        if summ:
            print(f"        {summ}")


def main():
    ap = argparse.ArgumentParser(description="Token-free pull retrieval over the knowledge wiki.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="print a fresh one-line-summary index").set_defaults(fn=cmd_index)
    sp = sub.add_parser("search", help="BM25 keyword search; pull the pages you need")
    sp.add_argument("query")
    sp.add_argument("-n", type=int, default=6, help="max results")
    sp.add_argument("--daily", action="store_true", help="also search daily logs")
    sp.set_defaults(fn=cmd_search)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
