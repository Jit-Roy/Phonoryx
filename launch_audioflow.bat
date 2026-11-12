@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m audioflow %*
if errorlevel 1 pause

