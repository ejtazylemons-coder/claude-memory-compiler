"""The plain-code linker that grows the Obsidian graph from each session note (scripts/linker.py).

Born 2026-10-10, the day the model compile was retired for good. No vault, no model: the article
list is handed in, so every rule is checked on text alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import linker  # noqa: E402

ARTICLES = [
    {"target": "concepts/memory-compiler", "title": "Memory Compiler",
     "terms": ["Memory Compiler", "flush.py"], "text": "memory compiler flush daily log notes"},
    {"target": "concepts/compiler-quota-cap", "title": "Compiler quota cap",
     "terms": ["Compiler"], "text": "quota cap budget guard weekly compile monthly fee waived scheduled"},
    {"target": "concepts/td-bank-transfers", "title": "TD Bank transfer types",
     "terms": ["TD Bank"], "text": "td bank dda scheduled transfer online xfer savings fee"},
]


def test_links_first_mention_only_and_longest_term_wins():
    text = "The memory compiler broke. The Memory Compiler was fixed. A compiler is a compiler."
    out, linked = linker.link_text(text, ARTICLES)
    assert out.count("[[concepts/memory-compiler|memory compiler]]") == 1
    assert "The [[concepts/memory-compiler|memory compiler]] broke. The Memory Compiler was fixed." in out
    # "compiler" alone still links the shorter article once, "memory compiler" was not split
    assert out.count("[[concepts/compiler-quota-cap|compiler]]") == 1
    assert linked == ["concepts/memory-compiler", "concepts/compiler-quota-cap"]


def test_code_links_and_urls_are_left_alone():
    text = ("Run `flush.py` first. See [[concepts/td-bank-transfers]] and "
            "[flush.py](https://example.com/flush.py) at https://example.com/TD Bank\n"
            "```\nflush.py TD Bank\n```\nThen flush.py again.")
    out, linked = linker.link_text(text, ARTICLES)
    assert "`flush.py`" in out
    assert "[[concepts/td-bank-transfers]]" in out
    assert "[flush.py](https://example.com/flush.py)" in out
    assert "```\nflush.py TD Bank\n```" in out
    assert out.endswith("Then [[concepts/memory-compiler|flush.py]] again.")
    assert linked == ["concepts/memory-compiler"]


def test_second_pass_changes_nothing():
    text = "TD Bank once. TD Bank twice. Memory Compiler here, memory compiler there."
    once, linked = linker.link_text(text, ARTICLES)
    assert once.count("[[") == 2 and linked == ["concepts/td-bank-transfers", "concepts/memory-compiler"]
    twice, linked_again = linker.link_text(once, ARTICLES)
    assert twice == once and linked_again == []
    # an article the note already links by hand is not linked a second time
    out, linked = linker.link_text("See [[concepts/td-bank-transfers|the bank]]. TD Bank again.", ARTICLES)
    assert linked == [] and out.endswith("TD Bank again.")


def test_whole_words_only():
    out, linked = linker.link_text("The compilers and recompiler are not it.", ARTICLES)
    assert linked == []
    assert "[[" not in out


def test_generic_and_short_terms_are_not_link_terms():
    assert not linker._usable("ops")
    assert not linker._usable("Claude Code")
    assert not linker._usable("12345")
    assert linker._usable("Memory Compiler")


def test_related_line_added_once_and_skips_inline_links():
    note = "**Facts:**\n- TD Bank savings fee is waived above $300; a DDA scheduled transfer hides from the app."
    out = linker.link_note(note, ARTICLES)
    assert "[[concepts/td-bank-transfers|TD Bank]]" in out
    assert "**Related:**" in out
    tail = out.split("**Related:**")[1]
    assert "td-bank-transfers" not in tail  # named inline, so not repeated as Related
    assert "[[concepts/compiler-quota-cap|Compiler quota cap]]" in tail  # shares fee / waived / scheduled
    again = linker.link_note(out, ARTICLES)
    assert again.count("**Related:**") == 1
    assert again.count("[[concepts/td-bank-transfers") == 1


def test_frontmatter_reader_handles_inline_and_block_aliases():
    meta, body = linker._frontmatter('---\ntitle: "A Title"\naliases: [one, "two"]\ntags:\n  - x\n---\n# A Title\nbody')
    assert meta["title"] == "A Title"
    assert meta["aliases"] == ["one", "two"]
    assert meta["tags"] == ["x"]
    assert body.startswith("# A Title")


def test_backfill_touches_session_sections_only(tmp_path, monkeypatch):
    monkeypatch.setattr(linker, "load_articles", lambda: ARTICLES)
    note = ("# Daily Log: 2026-10-10\n\n## Sessions\n\n"
            "### Git Activity (09:00)\n\n- fix flush.py\n\n"
            "### Session (13:27)\n\n**Context:** TD Bank transfers\n\n"
            "### Memory Flush (13:42)\n\nFLUSH_OK - Nothing worth saving from this session\n\n")
    (tmp_path / "2026-10-10.md").write_text(note, encoding="utf-8")
    rows = linker.backfill(apply=True, daily_dir=tmp_path)
    assert rows[0][1] >= 1
    out = (tmp_path / "2026-10-10.md").read_text(encoding="utf-8")
    assert "- fix flush.py\n" in out  # git section untouched
    assert "[[concepts/td-bank-transfers|TD Bank]]" in out
    assert "### Memory Flush (13:42)\n\nFLUSH_OK" in out
    # second pass adds nothing
    assert linker.backfill(apply=True, daily_dir=tmp_path)[0][1] == 0
