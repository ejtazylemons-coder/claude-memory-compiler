"""
SessionStart hook - a small, fixed piece of context at the start of every conversation.

What it injects (about 3 KB): the Homebase red/green gate banner when it is not green, today's
date, the tail of the most recent daily note, and one line about the knowledge base (how many
session notes and articles exist, how old the newest note is, and how to search them).

2026-10-10: the BM25 pull that injected five search hits here is gone. In 30 terminal logs no
pulled page was ever cited, and the fixed fallback query surfaced empty days. History is now
searched on demand, when a question needs it (mem.py search). Decision and sources:
workspace/research/memory-setup-2026-10-10.md.

Phase 3 (Memory Spine): reads the Homebase authoritative red/green verdict and, when RED +
reachable with no live break-glass token, prepends a loud BLOCKING banner (failing checks + the
exact break-glass command). Graceful degrade: an unreachable Homebase never blocks (AC3, spec
section 4.2.6).

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "command": "uv run python hooks/session-start.py"
        }]
    }
}
"""

import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Knowledge base lives in Obsidian vault
KB_ROOT = Path("C:/Obsidian/Second Brain/Claude/Knowledge")
KNOWLEDGE_DIR = KB_ROOT / "knowledge"
DAILY_DIR = KB_ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"

MAX_CONTEXT_CHARS = 8_000   # 2026-09-28: was 20_000, and the index dump ate all of it
MAX_LOG_LINES = 30
STALE_NOTE_DAYS = 8         # the canary: say so when the flush has not written a note in this long

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
MEM_PY = ROOT / "mem.py"
RETRIEVAL_BEACON = SCRIPTS_DIR / "retrieval-pull.beacon.json"


def _write_retrieval_beacon(summary: str, exit_code: int = 0) -> None:
    """Write the retrieval-pull heartbeat token (spine beacon contract, advisory row).

    Shape matches the other spine beacons: {"name", "machine", "last_run", "exit_code",
    "summary"}. Silently no-ops on any I/O failure; never blocks session start.
    """
    try:
        beacon = {
            "name": "retrieval-pull",
            "machine": socket.gethostname(),
            "last_run": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "summary": summary,
        }
        SCRIPTS_DIR.mkdir(exist_ok=True)
        RETRIEVAL_BEACON.write_text(json.dumps(beacon, indent=2), encoding="utf-8")
    except Exception:
        pass  # never block session start


def _gate_banner() -> str:
    """Phase 3 break-glass gate: read Homebase's authoritative verdict and decide.

    Returns a banner string to prepend to the injected context:
      - BLOCK  (RED + reachable, no live token): loud wall + failing checks + the
        exact break-glass command.
      - WARN   (operating under break-glass, OR Homebase unreachable): a notice,
        but session proceeds (laptop-off != stage-dead, spec section 4.2.6).
      - ALLOW  (green): empty string.

    Never raises: a broken gate must not crash session start (the gate degrades,
    it does not trap Mr.TL).
    """
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import break_glass

        decision = break_glass.gate_decision()
    except Exception:
        return ""  # gate failure never blocks session start

    if decision["decision"] == "block":
        return break_glass.render_block_message(decision).strip()
    if decision["decision"] == "warn":
        return f"## Memory Spine - WARN\n\n{decision['message']}"
    return ""  # allow: stay quiet on green


def get_recent_log() -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Return last N lines to keep context small
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)

    return "(no recent daily log)"


def _article_count() -> int:
    try:
        return sum(1 for p in KNOWLEDGE_DIR.rglob("*.md") if p.name.lower() not in ("index.md", "log.md"))
    except OSError:
        return 0


def _daily_notes() -> tuple[int, str, int]:
    """(count, newest date as YYYY-MM-DD, age in days). Dates come from the file names."""
    try:
        names = sorted(p.stem for p in DAILY_DIR.glob("????-??-??.md"))
    except OSError:
        names = []
    if not names:
        return 0, "", -1
    newest = names[-1]
    try:
        age = (datetime.now().date() - datetime.strptime(newest, "%Y-%m-%d").date()).days
    except ValueError:
        age = -1
    return len(names), newest, age


def knowledge_line() -> str:
    """The one line that replaced the five injected search hits."""
    count, newest, age = _daily_notes()
    line = (
        f"## Knowledge Base\n{count} session notes (newest {newest or 'none'}) and "
        f"{_article_count()} articles in the Obsidian wiki. Nothing is pre-loaded; when a question "
        f"needs what an earlier session found, search: "
        f'`python {MEM_PY} search "<terms>" -n 5` (zero tokens, half a second).'
    )
    if age > STALE_NOTE_DAYS:
        line += (f"\n\nCANARY: the newest session note is {age} days old. The end-of-session flush "
                 f"may have stopped; check {SCRIPTS_DIR / 'flush.log'}.")
    return line


def build_context(gate_banner: str = "") -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Phase 3 break-glass gate banner (block/warn) goes FIRST so it survives the
    # tail-truncation below and lands at the top of the injected context.
    if gate_banner:
        parts.append(gate_banner)

    # Today's date
    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    # Recent daily log (tail only)
    recent_log = get_recent_log()
    parts.append(f"## Recent Daily Log\n\n{recent_log}")

    # One line about the knowledge base; search on demand.
    parts.append(knowledge_line())

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def main():
    count, newest, age = _daily_notes()
    # Heartbeat first, so the token exists even if context assembly fails for an unrelated reason
    _write_retrieval_beacon(f"pointer only; {count} notes, newest {newest}, search on demand")

    # Phase 3: read Homebase's authoritative red/green verdict and gate.
    gate_banner = _gate_banner()

    context = build_context(gate_banner)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
