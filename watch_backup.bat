@echo off
cd /d "%~dp0"
echo.
echo ============================================
echo  ANE Auto Backup Watcher
echo ============================================
echo.
echo  Watching:  frontend\  backend\  CLAUDE.md  docs\
echo  Backup to: .backups\YYYY-MM-DD_HHMMSS\
echo.
echo  Close this window to stop.
echo ============================================
echo.

:BACKUP_NOW
set "_ts=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "_ts=%_ts: =0%"
set "_dir=.backups\%_ts%"
mkdir "%_dir%" 2>nul

xcopy /e /i /y /q frontend "%_dir%\frontend\" >nul 2>&1
xcopy /e /i /y /q backend "%_dir%\backend\" >nul 2>&1
if exist CLAUDE.md copy /y CLAUDE.md "%_dir%\" >nul 2>&1
if exist ane.bat copy /y ane.bat "%_dir%\" >nul 2>&1

echo  [%_ts%] Backup saved to .backups\%_ts%

:WAIT_15S
timeout /t 15 /nobreak >nul 2>&1

rem Count files to see if anything changed
set "_new_count=0"
for /f %%f in ('dir /s /a-d frontend\ backend\ 2^>nul ^| find "File(s)"') do set /a _new_count+=%%f 2>nul

goto BACKUP_NOW
