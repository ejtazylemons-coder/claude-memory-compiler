"""linker.py - plain-code wikilinks for session notes. No model, no scheduler.

2026-10-10: the weekly model compile that used to grow the Obsidian graph is retired for good
(research/memory-setup-2026-10-10.md in the workspace). This keeps the graph growing anyway:
when flush.py saves a session note, mentions of existing article titles and aliases become
[[links]], and a Related line adds the closest articles by BM25. It runs inside the flush, so
there is nothing new to schedule and nothing new that can die quietly.

Rules, so the notes stay readable:
  - whole-word, case-insensitive matches on a title or alias of at least MIN_TERM characters
  - never inside code spans, fenced blocks, existing [[links]], markdown links or URLs
  - one inline link per article per note, longest term wins, at most MAX_INLINE per note
  - Related: up to MAX_RELATED articles not already linked, scored by BM25 over articles only

Usage (backfill of existing daily notes; Session sections only):
    python scripts/linker.py backfill            # dry run: counts per note
    python scripts/linker.py backfill --apply    # write the links
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import DAILY_DIR, KNOWLEDGE_DIR  # noqa: E402

MIN_TERM = 5
MAX_INLINE = 12
MAX_RELATED = 5
RELATED_FLOOR = 0.6  # keep a Related hit only if it scores at least this share of the top hit
SUMMARY_CHARS = 900  # Related is scored on an article's subject (title + opening), not its whole body
MIN_OVERLAP = 3      # ...and the note must share at least this many distinct words with that subject

# Words that show up in nearly every note; linking them would tie every note to one random article.
GENERIC = {
    "claude", "claude code", "python", "windows", "github", "obsidian", "session", "memory",
    "hooks", "script", "scripts", "config", "settings", "server", "laptop", "desktop", "project",
    "workspace", "terminal", "dashboard", "daily log", "knowledge base",
}

FRONT = re.compile(r"^---\s*$")
# Segments the linker must not touch: fenced code, inline code, [[wikilinks]], [md](links), URLs.
PROTECTED = re.compile(
    r"```.*?```|`[^`\n]*`|\[\[[^\]]*\]\]|\[[^\]\n]*\]\([^)\n]*\)|https?://\S+",
    re.S,
)


def _frontmatter(text: str) -> tuple[dict, str]:
    """Tiny YAML reader for title and aliases (inline [a, b] or block list). Returns (meta, body)."""
    lines = text.splitlines()
    if not lines or not FRONT.match(lines[0]):
        return {}, text
    meta: dict = {}
    i, key = 1, None
    while i < len(lines) and not FRONT.match(lines[i]):
        ln = lines[i]
        m = re.match(r"^(\w+)\s*:\s*(.*)$", ln)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                meta[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
            elif val == "":
                meta[key] = []
            else:
                meta[key] = val.strip("\"'")
        elif key and re.match(r"^\s*-\s+", ln) and isinstance(meta.get(key), list):
            meta[key].append(re.sub(r"^\s*-\s+", "", ln).strip().strip("\"'"))
        i += 1
    return meta, "\n".join(lines[i + 1:])


def _usable(term: str) -> bool:
    t = term.strip()
    return len(t) >= MIN_TERM and re.search(r"[a-zA-Z]", t) is not None and t.lower() not in GENERIC


def load_articles(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[dict]:
    """Every article under knowledge/ (index.md and log.md excluded) with its link target,
    title, usable terms and body text."""
    out = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        if path.name.lower() in ("index.md", "log.md"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        meta, body = _frontmatter(text)
        title = meta.get("title") if isinstance(meta.get("title"), str) else None
        if not title:
            m = re.search(r"^#\s+(.+)$", body, re.M)
            title = m.group(1).strip() if m else path.stem.replace("-", " ")
        aliases = meta.get("aliases") if isinstance(meta.get("aliases"), list) else []
        terms = []
        for t in [title, *aliases]:
            if _usable(t) and t.lower() not in {x.lower() for x in terms}:
                terms.append(t.strip())
        target = path.relative_to(knowledge_dir).with_suffix("").as_posix()
        # Long hub articles mention everything; scoring on the opening keeps Related about the subject.
        summary = " ".join([title, *aliases, body[:SUMMARY_CHARS]])
        out.append({"target": target, "title": title, "terms": terms, "text": text, "summary": summary})
    return out


def _term_regex(articles: list[dict]) -> tuple[re.Pattern | None, dict]:
    by_term = {}
    for a in articles:
        for t in a["terms"]:
            by_term.setdefault(t.lower(), a["target"])  # first article wins a shared alias
    if not by_term:
        return None, {}
    terms = sorted(by_term, key=len, reverse=True)  # longest first: "memory compiler" before "compiler"
    pat = re.compile(
        r"(?<![\w\[|/#-])(" + "|".join(re.escape(t) for t in terms) + r")(?![\w\]|/-])",
        re.I,
    )
    return pat, by_term


def link_text(text: str, articles: list[dict]) -> tuple[str, list[str]]:
    """Inline-link the first mention of each article. Returns (new_text, linked_targets)."""
    pat, by_term = _term_regex(articles)
    if pat is None:
        return text, []
    # Articles the note already links (a re-run, or the model wrote one) are not linked again,
    # otherwise a second pass would link every article's second mention.
    linked: list[str] = list(dict.fromkeys(re.findall(r"\[\[([^\]|#]+)", text)))
    seeded = len(linked)

    def sub(m: re.Match) -> str:
        target = by_term.get(m.group(1).lower())
        if not target or target in linked or len(linked) >= MAX_INLINE:
            return m.group(0)
        linked.append(target)
        return f"[[{target}|{m.group(1)}]]"

    out, pos = [], 0
    for prot in PROTECTED.finditer(text):
        out.append(pat.sub(sub, text[pos:prot.start()]))
        out.append(prot.group(0))
        pos = prot.end()
    out.append(pat.sub(sub, text[pos:]))
    return "".join(out), linked[seeded:]


def related(text: str, articles: list[dict], exclude: list[str]) -> list[dict]:
    """Closest articles by BM25 over article subjects, minus the ones already linked inline."""
    import mem  # the token-free retriever next to this repo's root

    words = set(mem.tokenize(text)) - mem.STOP
    corpus = [(a["target"], a.get("summary") or a["text"]) for a in articles]
    overlap = {cid: len(words & set(mem.tokenize(body))) for cid, body in corpus}
    scored = [(s, t) for s, t in mem.bm25(text, corpus)
              if t not in exclude and overlap[t] >= MIN_OVERLAP]
    if not scored:
        return []
    top = scored[0][0]  # the floor is judged against the best article NOT already linked by name
    by_target = {a["target"]: a for a in articles}
    return [by_target[t] for s, t in scored[:MAX_RELATED] if s >= top * RELATED_FLOOR]


def link_note(text: str, articles: list[dict] | None = None) -> str:
    """The whole treatment for one session note: inline links, then a Related line."""
    if articles is None:
        articles = load_articles()
    if not articles:
        return text
    new_text, _ = link_text(text, articles)
    if "**Related:**" not in new_text:
        # everything linked inline, old or new, stays out of the Related line
        already = re.findall(r"\[\[([^\]|#]+)", new_text)
        picks = related(text, articles, exclude=already)
        if picks:
            line = " · ".join(f"[[{a['target']}|{a['title']}]]" for a in picks)
            new_text = new_text.rstrip() + f"\n\n**Related:** {line}"
    return new_text


SESSION_SECTION = re.compile(r"(?ms)^(### Session \([^)]*\)\n)(.*?)(?=^### |\Z)")


def backfill(apply: bool = False, daily_dir: Path = DAILY_DIR) -> list[tuple[str, int]]:
    """Link the Session sections of every existing daily note. Git Activity and Memory Flush
    sections are left alone. Idempotent: existing links are protected and a Related line is
    added once."""
    articles = load_articles()
    report = []
    for path in sorted(daily_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        added = 0

        def fix(m: re.Match) -> str:
            nonlocal added
            body = m.group(2)
            new = link_note(body.rstrip("\n"), articles)
            added += new.count("[[") - body.count("[[")
            return m.group(1) + new + ("\n\n" if body.endswith("\n\n") else "\n")

        new_text = SESSION_SECTION.sub(fix, text)
        if added and apply and new_text != text:
            path.write_text(new_text, encoding="utf-8")
        report.append((path.name, added))
    return report


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "backfill":
        apply = "--apply" in sys.argv
        rows = backfill(apply=apply)
        total = sum(n for _, n in rows)
        touched = sum(1 for _, n in rows if n)
        for name, n in rows:
            if n:
                print(f"{name}: +{n} links")
        print(f"{'wrote' if apply else 'would add'} {total} links across {touched} notes")
    else:
        print(__doc__)
