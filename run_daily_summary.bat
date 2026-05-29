@echo off
REM ==========================================================
REM run_daily_summary.bat - schickt die Tages-Status-Mail.
REM Wird von der geplanten Windows-Aufgabe aufgerufen.
REM ==========================================================
cd /d "%~dp0"
python daily_summary.py
