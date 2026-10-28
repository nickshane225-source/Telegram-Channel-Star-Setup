@echo off
setlocal enabledelayedexpansion
title StarPost Manager - Migrate Local MongoDB to Atlas

chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run run.bat first to initialize venv.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo ============================================================
echo   Step 1: Running Migration DRY RUN (No changes written)
echo ============================================================
echo.
venv\Scripts\python.exe scripts\migrate_local_to_atlas.py --report-duplicates %*
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Dry run reported an error. Please fix the issue above before applying.
    pause
    exit /b %errorlevel%
)

echo.
set /p CONFIRM="Do you want to APPLY this migration and copy data to the destination database now? (y/N): "
if /i "%CONFIRM%"=="y" (
    echo.
    echo ============================================================
    echo   Step 2: Applying Migration (--apply)
    echo ============================================================
    venv\Scripts\python.exe scripts\migrate_local_to_atlas.py --apply %*
) else (
    echo.
    echo [INFO] Migration cancelled. No changes were written.
)

echo.
pause
