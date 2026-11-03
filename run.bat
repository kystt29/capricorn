@echo off
cd /d "%~dp0"
set "PYW="
for %%v in (3.13 3.12 3.11 3.14) do if not defined PYW py -%%v -c "" >nul 2>nul && set "PYW=pyw -%%v"
if not defined PYW where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW set "PYW=pythonw"
start "" %PYW% main.py
