@echo off
rem Life House - local launcher.
rem
rem This file is deliberately ASCII-only. cmd.exe reads a .bat byte by byte
rem using the console codepage (950/Big5 on Traditional Chinese Windows).
rem UTF-8 Chinese bytes are then mis-read as Big5 double-byte characters,
rem which desyncs the parser: "echo" becomes "ho", and Chinese filenames in
rem the for-loop never match, so it reports missing files that do exist.
rem All user-facing messages live in launcher.py, where Python handles UTF-8.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Please run:
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

".venv\Scripts\python.exe" launcher.py
if errorlevel 1 pause
