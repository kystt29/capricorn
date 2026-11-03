@echo off
cd /d "%~dp0"
set "PY="
for %%v in (3.13 3.12 3.11 3.14) do if not defined PY py -%%v -c "" >nul 2>nul && set "PY=py -%%v"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [Capri] Python not found. Run setup.bat first.
  pause
  exit /b 1
)
%PY% tools\install_ai.py
pause
