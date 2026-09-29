"""The gate between the flush model and the daily log (flush.looks_like_notes).

Born 2026-09-29, the day the model answered a flush with a made-up continuation of the
chat and it was saved as a session. No model call here; the gate is pure text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from flush import looks_like_notes  # noqa: E402


def test_real_notes_pass():
    text = (
        "**Context:** MP previewer, the approve tap looked dead\n\n"
        "**Lessons Learned:**\n- A cron every 10 minutes is a dead button; a path unit fires on the inbox write.\n"
    )
    ok, why = looks_like_notes(text)
    assert ok, why


def test_notes_may_skip_the_context_line():
    ok, why = looks_like_notes("**Debugging Notes:**\n- Error 1014 on a Pages custom domain is a CNAME typo.\n")
    assert ok, why


def test_a_continued_transcript_is_rejected():
    text = (
        "the reviews also should have a slider or something\n\n"
        "**Assistant:** Building it: a review slider.\n\n"
        "**User:** ok make it live\n\n"
        "**Assistant:** Live at torringtonpt.com\n"
    )
    ok, why = looks_like_notes(text)
    assert not ok
    assert why == "does not open with a notes section"


def test_speaker_labels_inside_notes_are_rejected():
    text = "**Context:** the MP site\n\n**User:** ok make it live\n\n**Assistant:** Live.\n"
    ok, why = looks_like_notes(text)
    assert not ok
    assert "speaker labels" in why


def test_prose_without_a_section_is_rejected():
    ok, _ = looks_like_notes("Today we fixed the previewer and shipped the reviews section.")
    assert not ok
