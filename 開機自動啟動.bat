@echo off
rem Life House - bring the public demo up when the machine comes up.
rem
rem A shortcut to this file lives in the current user's Startup folder, so it
rem runs at logon. The "LifeHouse API" scheduled task runs it again at 08:00
rem as a safety net for days when the machine is already on and logged in.
rem
rem ASCII-only and CRLF-only on purpose. cmd.exe reads a batch file byte by
rem byte using the console codepage (950/Big5 on this machine), so a single
rem UTF-8 Chinese character in a comment desyncs the parser and even "rem"
rem stops being recognised - the symptom is a burst of errors like
rem "'Life' is not recognized as an internal or external command".
rem That is why this file never spells out the Chinese filenames around it,
rem and why .gitattributes pins *.bat and *.cmd to eol=crlf: a clone must not
rem be able to quietly turn these line endings back into LF.
setlocal
cd /d "%~dp0"

rem Log beside the project, not under %LOCALAPPDATA%: this task runs as
rem SYSTEM, where that variable points at the service profile deep under
rem C:\Windows\System32 - a place nobody thinks to look when the demo is
rem down and there are five minutes left before judging.
if not exist "%~dp0logs" mkdir "%~dp0logs"
set "LOG=%~dp0logs\serve.log"
set "TS=%ProgramFiles%\Tailscale\tailscale.exe"

rem ---- 1. Republish the public URL -----------------------------------------
rem Right after boot, tailscaled may not have finished connecting, so wait for
rem it (up to ~60s) before asking for the Funnel. Using ping as the sleep:
rem "timeout" needs a console and fails when this runs without one.
if exist "%TS%" (
  for /l %%i in (1,1,20) do (
    "%TS%" status >nul 2>&1
    if not errorlevel 1 goto :ts_ready
    ping -n 4 127.0.0.1 >nul 2>&1
  )
  echo [%DATE% %TIME%] [WARN] tailscale did not come up within 60s >> "%LOG%"
)
:ts_ready
rem Idempotent: if the Funnel config survived the reboot this changes nothing.
rem If it did not, this is what puts the public URL back.
if exist "%TS%" "%TS%" funnel --bg 8000 >nul 2>&1

rem ---- 2. Start the API ----------------------------------------------------
rem The Startup shortcut and the 08:00 task can both fire on the same day. A
rem second uvicorn would just lose the race for port 8000, so check first.
netstat -an | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [%DATE% %TIME%] port 8000 already serving, nothing to do >> "%LOG%"
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  echo [%DATE% %TIME%] [ERROR] .venv not found >> "%LOG%"
  exit /b 1
)

echo [%DATE% %TIME%] starting uvicorn on 127.0.0.1:8000 >> "%LOG%"

rem Bind 127.0.0.1, never 0.0.0.0: the public entry point is Tailscale Funnel,
rem which connects to localhost. Binding the LAN would hand an unauthenticated
rem API to every device on the network - see the note at the end of Makefile.
".venv\Scripts\python.exe" -m uvicorn src.serving.api:app --host 127.0.0.1 --port 8000 >> "%LOG%" 2>&1

echo [%DATE% %TIME%] uvicorn exited with %ERRORLEVEL% >> "%LOG%"
