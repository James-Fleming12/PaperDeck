@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel%==0 (
  uv sync --extra rerank
  uv run paperdeck serve --open %*
  exit /b
)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install --quiet -e ".[rerank]"
paperdeck serve --open %*
