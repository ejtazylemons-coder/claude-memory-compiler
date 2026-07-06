' weekly-lint-shim.vbs - launch weekly-lint.ps1 with NO visible window.
' `powershell -WindowStyle Hidden` still flashes (console allocated before hide);
' wscript + Run bWindowStyle=0 never shows one. Same pattern as
' workspace_sync/push_lola_health_shim.vbs. CMD-popup purge 2026-07-06.
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -NonInteractive -File ""C:\Dev\claude-memory-compiler\scripts\weekly-lint.ps1""", 0, True
