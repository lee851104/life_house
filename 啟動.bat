@echo off
rem Life House 安居指數 — 本機啟動
rem 用法：直接雙擊，或在此資料夾執行  啟動.bat
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [錯誤] 找不到 .venv，請先執行：
  echo        python -m venv .venv
  echo        .venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" numpy scipy osmium shapely pyproj
  pause
  exit /b 1
)
for %%F in ("事故索引.db" "路網.npz" "市界.npz" "基準.npz") do (
  if not exist %%F (
    echo [錯誤] 找不到 %%F，請依序執行：
    echo        .venv\Scripts\python.exe 下載資料.py
    echo        .venv\Scripts\python.exe 篩選縣市.py 桃園市
    echo        .venv\Scripts\python.exe 建立索引.py
    echo        .venv\Scripts\python.exe 建立路網.py
    echo        .venv\Scripts\python.exe 建立市界.py
    echo        .venv\Scripts\python.exe 建立基準.py
    pause
    exit /b 1
  )
)

echo 啟動中… 服務準備完成後會自動開啟 http://127.0.0.1:8000
".venv\Scripts\python.exe" launcher.py
