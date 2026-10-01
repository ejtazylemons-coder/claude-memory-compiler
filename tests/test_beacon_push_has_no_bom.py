"""The beacon push in run-monthly-state-synthesis.bat must not go through a pipe.

Homebase parses this beacon with Python json.loads (ops/daily/checks/beacon_healthy.py),
which rejects a UTF-8 BOM outright - a deliberate contract, recorded in ADR-019 and pinned
on the reader side by ops/tests/test_exit_code_contract.py::test_bom_beacon_is_rejected.

Four times now a BOM has crept into this particular beacon. The 2026-08-27 attempt set
$OutputEncoding = UTF8Encoding($false) and kept `$body | ssh homebase 'cat > ...'`; that
held for the Sep 1 run and silently produced a BOM again on Oct 1, turning the Ops check
red while the synthesis itself was healthy. The encoding of a PowerShell -> native-command
pipe is simply not a dependable knob.

So the rule this file pins: write the bytes with an explicit BOM-less encoder and ship the
file, never pipe the string. No PowerShell is executed here; the gate is pure text.
"""
from pathlib import Path

BAT = Path(__file__).resolve().parent.parent / "scripts" / "run-monthly-state-synthesis.bat"


def _text() -> str:
    return BAT.read_text(encoding="utf-8")


def test_bat_exists_and_is_itself_bom_free():
    # A BOM on the .bat would be fed to cmd as part of the first command.
    assert BAT.exists(), f"missing {BAT}"
    assert not BAT.read_bytes().startswith(b"\xef\xbb\xbf"), "the .bat itself has a BOM"


def test_beacon_is_not_piped_into_ssh():
    """The regression that bit on 2026-10-01. Piping leaves the encoding to $OutputEncoding."""
    text = _text()
    assert "| ssh" not in text, "beacon is piped into ssh again; encoding is not dependable"
    assert "$body |" not in text, "beacon body is piped; write the bytes to a file instead"


def test_beacon_is_written_with_an_explicit_bom_less_encoder():
    text = _text()
    assert "WriteAllText" in text, "beacon must be written as bytes, not piped"
    assert "UTF8Encoding($false)" in text, "beacon encoder must be UTF-8 with BOM suppressed"
    # $false is what suppresses the preamble; UTF8Encoding() alone would emit a BOM.
    assert "New-Object System.Text.UTF8Encoding($true)" not in text


def test_beacon_is_shipped_by_scp():
    assert "scp -B -q" in _text(), "beacon file should be copied with scp -B -q (batch, quiet)"


def test_beacon_still_reports_the_synthesis_exit_code():
    """The push must carry the real RC: exit_code is the whole RED/YELLOW contract."""
    text = _text()
    assert "exit_code = %RC%" in text, "beacon must report the synthesis exit code, not a literal"
    assert "set RC=%ERRORLEVEL%" in text
    assert text.rstrip().endswith("exit /b %RC%"), "the .bat must exit with the synthesis RC"


def test_continuation_lines_are_well_formed():
    """A dropped `^` or an odd quote silently truncates the PowerShell command."""
    lines = _text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("powershell "))
    block = [lines[start]]
    while block[-1].rstrip().endswith("^"):
        block.append(lines[start + len(block)])

    for line in block[:-1]:
        assert line.rstrip().endswith("^"), f"continuation line lost its caret: {line!r}"
    assert not block[-1].rstrip().endswith("^"), "continuation runs past the end of the block"
    for line in block:
        body = line.rstrip().rstrip("^")
        assert body.count('"') % 2 == 0, f"unbalanced quotes in: {line!r}"
