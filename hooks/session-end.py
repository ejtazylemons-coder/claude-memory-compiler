"""
SessionEnd hook - captures conversation transcript for memory extraction.

When a Claude Code session ends, this hook reads the transcript path from
stdin, extracts conversation context, and spawns flush.py as a background
process to extract knowledge into the daily log.

The hook itself does NO API calls - only local file I/O for speed (<10s).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard: if we were spawned by flush.py (which calls Agent SDK,
# which runs Claude Code, which would fire this hook again), exit immediately.
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
KB_ROOT = Path("C:/Obsidian/Second Brain/Claude/Knowledge")
DAILY_DIR = KB_ROOT / "daily"
SCRIPTS_DIR = ROOT / "scripts"
STATE_DIR = SCRIPTS_DIR

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "flush.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [hook] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_TURNS = 30
MAX_CONTEXT_CHARS = 15_000
MIN_TURNS_TO_FLUSH = 1


def extract_conversation_context(transcript_path: Path) -> tuple[str, int]:
    """Read JSONL transcript and extract last ~N conversation turns as markdown."""
    turns: list[str] = []

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                label = "User" if role == "user" else "Assistant"
                turns.append(f"**{label}:** {content.strip()}\n")

    recent = turns[-MAX_TURNS:]
    context = "\n".join(recent)

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
        boundary = context.find("\n**")
        if boundary > 0:
            context = context[boundary + 1 :]

    return context, len(recent)


# Managed repo paths — sync with C:\Dev\workspace\workspace_sync\managed_repos.psd1 if repos change
MANAGED_REPOS: list[tuple[str, Path]] = [
    ("workspace", Path("C:/Dev/workspace")),
    ("olympus", Path("C:/Dev/olympus")),
    ("cris", Path("C:/Dev/cris")),
    ("pantheon", Path("C:/Dev/pantheon")),
    ("codex", Path("C:/Dev/codex")),
    ("server-projects", Path("C:/Dev/server-projects")),
    ("hidden-mechanics-lab", Path("C:/Dev/hidden-mechanics-lab")),
    ("mayhem-motorsports", Path("C:/Dev/mayhem-motorsports")),
    ("beacon", Path("C:/Dev/beacon")),
    ("claude-memory-compiler", Path("C:/Dev/claude-memory-compiler")),
]


def append_to_daily_log_raw(content: str, section: str) -> None:
    """Append raw content to today's daily log (no LLM — for git data)."""
    today = datetime.now(timezone.utc).astimezone()
    log_path = DAILY_DIR / f"{today.strftime('%Y-%m-%d')}.md"
    if not log_path.exists():
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# Daily Log: {today.strftime('%Y-%m-%d')}\n\n## Sessions\n\n## Memory Maintenance\n\n",
            encoding="utf-8",
        )
    time_str = today.strftime("%H:%M")
    entry = f"### {section} ({time_str})\n\n{content}\n\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def capture_git_activity() -> None:
    """Capture recent git commits across managed repos into the daily log."""
    import subprocess as _sp

    lines: list[str] = []
    for name, repo_path in MANAGED_REPOS:
        if not repo_path.exists():
            continue
        try:
            result = _sp.run(
                ["git", "log", "--oneline", "-5", "--since=12 hours ago"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            commits = result.stdout.strip()
            if commits:
                lines.append(f"**{name}:**")
                for line in commits.splitlines():
                    lines.append(f"  {line}")
        except Exception as e:
            logging.debug("git log failed for %s: %s", name, e)

    if not lines:
        return

    content = "\n".join(lines)
    try:
        append_to_daily_log_raw(content, "Git Activity")
        logging.info("Git activity captured: %d repos with commits", sum(1 for l in lines if l.startswith("**")))
    except Exception as e:
        logging.error("Failed to write git activity: %s", e)


def main() -> None:
    # Read hook input from stdin
    # Claude Code on Windows may pass paths with unescaped backslashes
    try:
        raw_input = sys.stdin.read()
        try:
            hook_input: dict = json.loads(raw_input)
        except json.JSONDecodeError:
            fixed_input = re.sub(r'(?<!\\)\\(?!["\\])', r'\\\\', raw_input)
            hook_input = json.loads(fixed_input)
    except (json.JSONDecodeError, ValueError, EOFError) as e:
        logging.error("Failed to parse stdin: %s", e)
        return

    session_id = hook_input.get("session_id", "unknown")
    source = hook_input.get("source", "unknown")
    transcript_path_str = hook_input.get("transcript_path", "")

    logging.info("SessionEnd fired: session=%s source=%s", session_id, source)

    # Dedup guard: if we already processed this session in the last 5 minutes, skip.
    # Prevents duplicate flush.py spawns that cause Agent SDK init-timeout races.
    import time
    lock_path = SCRIPTS_DIR / f".lock-{session_id}.flag"
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age < 300:
                logging.info("SKIP: session %s flushed %.1fs ago (dedup lock)", session_id, age)
                return
        except OSError:
            pass
    try:
        lock_path.write_text(str(int(time.time())), encoding="utf-8")
    except OSError as e:
        logging.warning("Could not write dedup lock: %s", e)

    if not transcript_path_str or not isinstance(transcript_path_str, str):
        logging.info("SKIP: no transcript path")
        return

    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        logging.info("SKIP: transcript missing: %s", transcript_path_str)
        return

    # Extract conversation context in the hook (fast, no API calls)
    try:
        context, turn_count = extract_conversation_context(transcript_path)
    except Exception as e:
        logging.error("Context extraction failed: %s", e)
        return

    if not context.strip():
        logging.info("SKIP: empty context")
        return

    if turn_count < MIN_TURNS_TO_FLUSH:
        logging.info("SKIP: only %d turns (min %d)", turn_count, MIN_TURNS_TO_FLUSH)
        return

    # Capture git activity directly to daily log (no LLM — raw commit data)
    capture_git_activity()

    # Write context to a temp file for the background process
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    context_file = STATE_DIR / f"session-flush-{session_id}-{timestamp}.md"
    context_file.write_text(context, encoding="utf-8")

    # Spawn flush.py as a background process via the venv python directly.
    # Previous wiring used a hardcoded uv.exe path (`C:\Users\Eric\.local\bin\uv.exe`)
    # which broke on any machine that wasn't Work PC. Fixed 2026-05-02 alongside the
    # VBS-wrapper-stdin-loss fix; venv python avoids both portability + spawn cost.
    flush_script = SCRIPTS_DIR / "flush.py"
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    cmd = [
        str(venv_python),
        str(flush_script),
        str(context_file),
        session_id,
    ]

    # On Windows, use CREATE_NO_WINDOW to avoid flash console window.
    # Do NOT use DETACHED_PROCESS — it breaks the Agent SDK's subprocess I/O.
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        logging.info("Spawned flush.py for session %s (%d turns, %d chars)", session_id, turn_count, len(context))
    except Exception as e:
        logging.error("Failed to spawn flush.py: %s", e)
        return

    # Write local beacon + SSH push to Homebase for Ops worker `memory-compiler-flush`.
    # Fire-and-forget — must never crash the hook. Beacon staleness is what the
    # `memory-compiler-flush` worker watches for; if Telegram fires a stale alert,
    # see runbook `Harness/runbooks/memory-compiler-flush-stale.md`.
    publish_beacon(session_id, turn_count, len(context))


def publish_beacon(session_id: str, turn_count: int, ctx_chars: int) -> None:
    """Write local beacon JSON + SSH-push to Homebase. Failures are logged, not raised."""
    import platform
    beacon_local = SCRIPTS_DIR / "last-flush.beacon.json"
    beacon_remote = "/root/hestia/beacons/memory-compiler-flush.json"
    push_log = SCRIPTS_DIR / "beacon-push.log"

    now_ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    payload = {
        "name": "memory-compiler-flush",
        "machine": platform.node(),
        "last_run": now_ts,
        "exit_code": 0,
        "summary": f"flush spawned for session {session_id[:8]} ({turn_count} turns, {ctx_chars} chars)",
        "version": "1.0.0",
    }
    body = json.dumps(payload, separators=(",", ":"))

    try:
        beacon_local.write_text(body, encoding="utf-8")
    except OSError as e:
        logging.error("Beacon: failed to write local file: %s", e)
        return

    # SSH push — fire-and-forget. Never block more than a few seconds.
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             "homebase", f"cat > {beacon_remote}"],
            input=body,
            text=True,
            capture_output=True,
            timeout=10,
            creationflags=creation_flags,
        )
        if result.returncode == 0:
            with open(push_log, "a", encoding="utf-8") as f:
                f.write(f"{now_ts} pushed OK\n")
        else:
            with open(push_log, "a", encoding="utf-8") as f:
                f.write(f"{now_ts} push FAIL rc={result.returncode} err={result.stderr.strip()[:200]}\n")
    except Exception as e:
        try:
            with open(push_log, "a", encoding="utf-8") as f:
                f.write(f"{now_ts} push ERROR {type(e).__name__}: {str(e)[:200]}\n")
        except OSError:
            pass


if __name__ == "__main__":
    main()

