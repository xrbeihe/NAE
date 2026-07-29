@echo off
cd /d "%~dp0"

:MENU
echo ============================================
echo     AI Narrative Engine Service Manager
echo ============================================
echo.
echo  1. Start backend (waiting for ready)
echo  2. Backend only (foreground)
echo  3. Stop services
echo  4. Clean cache
echo  5. Stop + clean cache
echo  6. Remove old DB and rebuild
echo  7. Exit
echo.
set /p choice="Select [1-7] (default 1): "
if "%choice%"=="" set choice=1

if "%choice%"=="1" goto START_ALL
if "%choice%"=="2" goto START_BACKEND
if "%choice%"=="3" goto STOP
if "%choice%"=="4" goto CLEAN
if "%choice%"=="5" goto STOP_CLEAN
if "%choice%"=="6" goto REBUILD_DB
if "%choice%"=="7" goto EXIT
echo Invalid option
timeout /t 2 >nul
goto MENU

:START_ALL
echo.
echo [1/2] Killing old processes...
call :KILL_PORT

echo [2/2] Starting backend (waiting for ready)...
cd backend
set ANE_RELOAD=1
set ANE_PORT=8002
start /b "" "%~dp0.venv\Scripts\python.exe" -m ane.main
cd ..

echo     Waiting for backend to be ready...
:WAIT_BACKEND
timeout /t 2 /nobreak >nul
curl -s http://127.0.0.1:8002/api/health >nul 2>&1
if errorlevel 1 goto WAIT_BACKEND
echo     Backend ready!

echo.
echo ============================================
echo  ANE Server Running
echo ============================================
echo  Backend: http://localhost:8002
echo ============================================
echo.
echo  Close this window or run again + select 3 to stop.
echo.
timeout /t 5 >nul
exit

:START_BACKEND
echo.
echo Killing old backend...
call :KILL_PORT
echo Starting backend...
cd backend
set ANE_RELOAD=1
set ANE_PORT=8002
"%~dp0.venv\Scripts\python.exe" -m ane.main
cd ..
goto MENU

:STOP
echo.
echo Stopping services...
call :KILL_PORT
echo Done.
timeout /t 2 >nul
goto MENU

:CLEAN
echo.
echo Cleaning cache...
cd backend
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s/q "%%d" 2>nul
del /s/q *.pyc 2>nul
cd ..
echo Done.
timeout /t 2 >nul
goto MENU

:STOP_CLEAN
call :KILL_PORT
cd backend
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s/q "%%d" 2>nul
del /s/q *.pyc 2>nul
cd ..
echo Done.
timeout /t 2 >nul
goto MENU

:EXIT
exit /b

:REBUILD_DB
echo.
echo [1/3] Killing old processes...
call :KILL_PORT
echo [2/3] Removing old database...
if exist "data\ane.db" del /f data\ane.db 2>nul
if exist "data\ane.db-shm" del /f data\ane.db-shm 2>nul
if exist "data\ane.db-wal" del /f data\ane.db-wal 2>nul
echo [3/3] Rebuilding database on next server start...
echo Done.
timeout /t 2 >nul
goto MENU

:KILL_PORT
for %%p in (8002) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr LISTENING') do (
        taskkill /f /pid %%a >nul 2>&1
    )
)
exit /b
