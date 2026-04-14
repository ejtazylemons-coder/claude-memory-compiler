@echo off
cd /d C:\Dev\claude-memory-compiler
"C:\Users\Eric\.local\bin\uv.exe" run python scripts\weekly-rollup.py >> scripts\weekly-rollup.log 2>&1
