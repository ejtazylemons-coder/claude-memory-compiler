"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

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

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Knowledge base lives in Obsidian vault
KB_ROOT = Path("C:/Obsidian/Second Brain/Claude/Knowledge")
KNOWLEDGE_DIR = KB_ROOT / "knowledge"
DAILY_DIR = KB_ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"

MAX_CONTEXT_CHARS = 20_000
MAX_LOG_LINES = 30

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
STATE_FILE = SCRIPTS_DIR / "state.json"
UV = r"C:\Users\Eric\.local\bin\uv.exe"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _has_uncompiled_logs() -> bool:
    """Return True if any daily log file hasn't been compiled (or has changed since)."""
    if not DAILY_DIR.exists():
        return False
    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    ingested = state.get("ingested", {})
    for log_path in sorted(DAILY_DIR.glob("*.md")):
        prev = ingested.get(log_path.name, {})
        if not prev or prev.get("hash") != _file_hash(log_path):
            return True
    return False


def _spawn_compile_if_needed() -> None:
    """If uncompiled logs exist, spawn compile.py in the background (fire and forget).

    A global lockfile prevents overlapping compiles — critical because compile.py
    spawns the Claude Agent SDK which racing would cause 'Control request timeout:
    initialize' errors.
    """
    if not _has_uncompiled_logs():
        return
    compile_script = SCRIPTS_DIR / "compile.py"
    if not compile_script.exists():
        return

    # Global compile lock — skip if another compile is running or finished in last 10 min
    import time
    lock_path = SCRIPTS_DIR / ".compile-lock.flag"
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age < 600:
                return
        except OSError:
            pass
    try:
        lock_path.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass

    cmd = [UV, "run", "--directory", str(ROOT), "python", str(compile_script)]
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception:
        pass  # Never block session start due to compile errors


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


def build_context() -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Today's date
    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    # Knowledge base index (the core retrieval mechanism)
    if INDEX_FILE.exists():
        index_content = INDEX_FILE.read_text(encoding="utf-8")
        parts.append(f"## Knowledge Base Index\n\n{index_content}")
    else:
        parts.append("## Knowledge Base Index\n\n(empty - no articles compiled yet)")

    # Recent daily log
    recent_log = get_recent_log()
    parts.append(f"## Recent Daily Log\n\n{recent_log}")

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def main():
    # Catch up on any uncompiled daily logs before injecting context.
    # Runs in the background — never blocks session start.
    _spawn_compile_if_needed()

    context = build_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
