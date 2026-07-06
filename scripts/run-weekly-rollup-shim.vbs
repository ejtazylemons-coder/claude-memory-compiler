' run-weekly-rollup-shim.vbs - launch the weekly rollup batch with NO visible window.
' ClaudeWeeklyCompile task previously ran `cmd.exe /c run-weekly-rollup.bat` directly,
' which flashed a CMD window (Mr.TL popup complaint 2026-07-06). Same shim pattern as
' workspace_sync/push_lola_health_shim.vbs. Scheduled tasks have no stdin, so the
' 2026-05-02 "VBS swallowed hook stdin" incident does not apply here.
Set shell = CreateObject("WScript.Shell")
shell.Run """C:\Dev\claude-memory-compiler\scripts\run-weekly-rollup.bat""", 0, False
