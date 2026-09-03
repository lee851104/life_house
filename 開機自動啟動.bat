@echo off
rem Life House - serve the public demo in a VISIBLE window at logon.
rem
rem Put a SHORTCUT to this file in the Startup folder (Win+R -> shell:startup).
rem Do NOT copy the file there. This script uses %~dp0 to locate the project, so
rem a copy sitting in the Startup folder would look for .venv beside itself and
rem fail. A shortcut leaves %~dp0 pointing at the project folder.
rem
rem THIS WINDOW IS THE SERVICE. Closing it takes the public URL down.
rem
rem Everything this prints comes from src/serving/launcher.py. ASCII-only and
rem CRLF-only on purpose: cmd.exe reads a batch file byte by byte using the
rem console codepage (950/Big5 on this machine), so a single UTF-8 Chinese
rem character in a comment desyncs the parser and even "rem" stops being
rem recognised - the symptom is a burst of errors like "'Life' is not
rem recognized as an internal or external command". That is also why
rem .gitattributes pins *.bat and *.cmd to eol=crlf: a clone must not be able
rem to quietly turn these line endings back into LF.
setlocal
cd /d "%~dp0"
title Life House - public demo

rem The launcher prints Traditional Chinese; UTF-8 keeps it readable whether
rem stdout is a console or a redirected pipe.
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found under "%~dp0"
  echo.
  echo If you copied this file into the Startup folder, delete that copy and
  echo put a SHORTCUT there instead - this script must run from the project
  echo folder. Otherwise create the environment first:
  echo     python -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m src.serving.launcher --boot

echo.
echo [stopped] this machine is no longer serving the public demo.
pause
