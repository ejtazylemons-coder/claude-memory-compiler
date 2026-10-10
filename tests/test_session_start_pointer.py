"""The start-of-session context after 2026-10-10: no injected search hits, one knowledge line,
and a canary when the flush has gone quiet."""
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session-start.py"
spec = importlib.util.spec_from_file_location("session_start", HOOK)
session_start = importlib.util.module_from_spec(spec)
sys.modules["session_start"] = session_start
spec.loader.exec_module(session_start)


def _vault(tmp_path, monkeypatch, newest: date):
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / f"{newest.isoformat()}.md").write_text("# Daily Log\n\n### Session (10:00)\n\n**Facts:**\n- x\n", encoding="utf-8")
    (daily / "2026-01-01.md").write_text("# old\n", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    (knowledge / "concepts").mkdir(parents=True)
    (knowledge / "concepts" / "a.md").write_text("---\ntitle: A\n---\n# A\n", encoding="utf-8")
    (knowledge / "index.md").write_text("# index\n", encoding="utf-8")
    monkeypatch.setattr(session_start, "DAILY_DIR", daily)
    monkeypatch.setattr(session_start, "KNOWLEDGE_DIR", knowledge)


def test_no_pull_section_and_one_knowledge_line(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch, date.today())
    ctx = session_start.build_context()
    assert "Memory Pull" not in ctx
    assert "2 session notes" in ctx
    assert "1 articles" in ctx
    assert "Nothing is pre-loaded" in ctx
    assert "mem.py" in ctx
    assert "CANARY" not in ctx
    assert len(ctx) < 3000


def test_canary_when_the_flush_has_gone_quiet(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch, date.today() - timedelta(days=12))
    line = session_start.knowledge_line()
    assert "CANARY" in line
    assert "12 days old" in line


def test_hook_output_is_json_with_additional_context(tmp_path, monkeypatch, capsys):
    _vault(tmp_path, monkeypatch, date.today())
    monkeypatch.setattr(session_start, "_gate_banner", lambda: "")
    monkeypatch.setattr(session_start, "RETRIEVAL_BEACON", tmp_path / "beacon.json")
    monkeypatch.setattr(session_start, "SCRIPTS_DIR", tmp_path)
    session_start.main()
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Knowledge Base" in out["hookSpecificOutput"]["additionalContext"]
    beacon = json.loads((tmp_path / "beacon.json").read_text(encoding="utf-8"))
    assert beacon["name"] == "retrieval-pull" and beacon["exit_code"] == 0
    assert "search on demand" in beacon["summary"]
