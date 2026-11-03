@echo off
cd /d "%~dp0"
call :pick
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(sys.version_info >= (3, 15))" && goto install
echo [Capri] Found only Python 3.15 or newer - the voice library does not work with it yet.
echo [Capri] Installing Python 3.13 next to it...
py install 3.13
call :pick
%PY% -c "import sys; sys.exit(sys.version_info >= (3, 15))" && goto install
echo.
echo [Capri] Please install Python 3.13 manually: https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe
echo         then run setup.bat again.
pause
exit /b 1

:install
%PY% tools\install.py
pause
exit /b

:nopython
echo [Capri] Python not found. Install Python 3.13 from https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe
echo         and tick "Add python.exe to PATH" during installation.
pause
exit /b 1

:pick
rem Prefer versions Capri is tested with; Python 3.15+ breaks the voice library.
set "PY="
for %%v in (3.13 3.12 3.11 3.14) do if not defined PY py -%%v -c "" >nul 2>nul && set "PY=py -%%v"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
exit /b
