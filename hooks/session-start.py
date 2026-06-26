"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

Phase 4 (Memory Spine): also auto-runs mem.py BM25 pull-retrieval at session
start so the retriever is actually consulted, not just available (AC5).

Phase 3 (Memory Spine): reads the Homebase authoritative red/green verdict and,
when RED + reachable with no live break-glass token, prepends a loud BLOCKING
banner (failing checks + the exact break-glass command) to the injected context.
Graceful degrade: an unreachable Homebase never blocks (AC3, spec §4.2.6).

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
import re
import socket
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
MEM_PY = ROOT / "mem.py"
RETRIEVAL_BEACON = SCRIPTS_DIR / "retrieval-pull.beacon.json"

# Words stripped from daily-log-derived queries so rare, meaningful terms drive BM25
# (mirrors the STOP set in mem.py, extended with session-log noise words)
STOP_WORDS = set(
    "a an the of to in on for and or is are was were be do does did how "
    "what why when where which who that this it its with my our your you i "
    "we they he she them me work works working use used using about into "
    "session today yesterday update status notes log daily".split()
)


def _derive_query() -> str:
    """Extract query terms from recent daily log headings; fall back to a fixed default.

    Scans the first 20 lines of today's (or yesterday's) daily log for heading
    lines (# / ## / ###) and pulls meaningful non-stop words from them.  If
    fewer than 2 useful terms are found the fixed default covers the most common
    recurring session topics.
    """
    today = datetime.now(timezone.utc).astimezone()
    log_text = ""
    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
            break

    words = []
    if log_text:
        for line in log_text.splitlines()[:20]:
            if line.startswith("#"):
                clean = re.sub(r"^#+\s*", "", line)
                for word in re.findall(r"[a-z0-9]+", clean.lower()):
                    if word not in STOP_WORDS and len(word) > 2:
                        words.append(word)
            if len(words) >= 6:
                break

    if len(words) >= 2:
        return " ".join(words[:6])
    # fixed default: covers the recurring session topics most likely to surface
    # relevant wiki pages when the daily log is empty or heading-free
    return "memory compiler ops harness sync-up workflow"


def _run_mem_pull(query: str, n: int = 5):
    """Run mem.py search <query> -n <n> via stdlib subprocess (zero-token BM25).

    Returns (output_str, n_results, exit_code).  All exceptions are caught so
    a mem.py failure never blocks session start.
    """
    if not MEM_PY.exists():
        return ("(mem.py not found)", 0, 1)
    try:
        result = subprocess.run(
            [sys.executable, str(MEM_PY), "search", query, "-n", str(n)],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="ignore",
            cwd=str(ROOT),
        )
        out = result.stdout.strip()
        # score lines look like "[  4.12] **title**  `path`"
        n_found = sum(1 for ln in out.splitlines() if re.match(r"^\[\s*[\d]", ln))
        return (out, n_found, result.returncode)
    except subprocess.TimeoutExpired:
        return ("(mem.py search timed out)", 0, 1)
    except Exception as exc:
        return (f"(mem.py error: {exc})", 0, 1)


def _write_retrieval_beacon(n_pulled: int, exit_code: int) -> None:
    """Write the retrieval-pull heartbeat token (spine beacon contract).

    Shape matches the other spine beacons so a later reconciler can verify
    this stage ran: {"name", "machine", "last_run", "exit_code", "summary"}.
    Silently no-ops on any I/O failure — never blocks session start.
    """
    try:
        beacon = {
            "name": "retrieval-pull",
            "machine": socket.gethostname(),
            "last_run": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "summary": f"pulled {n_pulled} pages",
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
        but session proceeds (laptop-off != stage-dead, spec §4.2.6).
      - ALLOW  (green): empty string.

    Never raises — a broken gate must not crash session start (the gate degrades,
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
        return f"## Memory Spine — WARN\n\n{decision['message']}"
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


def build_context(pull_output: str = "", gate_banner: str = "") -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Phase 3 break-glass gate banner (block/warn) goes FIRST so it survives the
    # tail-truncation below and lands at the top of the injected context.
    if gate_banner:
        parts.append(gate_banner)

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

    # BM25 pull retrieval (Phase 4 — auto-consulted at session start, AC5)
    # Error strings from _run_mem_pull start with "(" and are not injected as context.
    if pull_output and not pull_output.startswith("("):
        parts.append(f"## Memory Pull (BM25)\n\n{pull_output}")

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def main():
    # Derive query from today's daily log headings (falls back to fixed default)
    query = _derive_query()
    pull_output, n_pulled, pull_exit = _run_mem_pull(query)
    # Write heartbeat before building context so the token exists even if
    # context assembly fails for an unrelated reason
    _write_retrieval_beacon(n_pulled, pull_exit)

    # Phase 3: read Homebase's authoritative red/green verdict and gate.
    gate_banner = _gate_banner()

    context = build_context(pull_output, gate_banner)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
