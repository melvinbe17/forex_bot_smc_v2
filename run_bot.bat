@echo off
REM ==========================================================
REM run_bot.bat - startet den Live-Runner und startet ihn bei
REM Absturz automatisch neu. Fenster schliessen = stoppen.
REM ==========================================================
cd /d "%USERPROFILE%\Desktop\forex_bot_smc"
:loop
echo ==========================================================
echo [%date% %time%] Starte live_runner --live ...
echo ==========================================================
python live_runner.py --live
echo [%date% %time%] Runner beendet (ExitCode %errorlevel%). Neustart in 15s ...
timeout /t 15 /nobreak >nul
goto loop
