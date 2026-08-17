' run-monthly-state-synthesis-shim.vbs - launch the monthly synthesis batch with NO
' visible window. ClaudeMonthlyStateSynthesis previously ran the .bat directly, which
' flashed a CMD window (Mr.TL complaint 2026-08-16, same class as the 2026-07-06 weekly
' rollup popup). Same shim pattern as run-weekly-rollup-shim.vbs.
Set shell = CreateObject("WScript.Shell")
shell.Run """C:\Dev\claude-memory-compiler\scripts\run-monthly-state-synthesis.bat""", 0, False
