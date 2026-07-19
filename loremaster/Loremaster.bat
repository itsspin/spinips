@echo off
rem Spin's Loremaster — launcher for Windows.
rem Requires Python 3.10+ from python.org (tkinter included).
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "Spin's Loremaster" pythonw loremaster.py %*
) else (
    python loremaster.py %*
)
