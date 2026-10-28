@echo off
setlocal enabledelayedexpansion
title StarPost Manager Launcher

:: Set console to UTF-8
chcp 65001 >nul

:: Move to script directory
cd /d "%~dp0"

echo ============================================================
echo   StarPost Manager - Starting Local Studio
echo ============================================================
echo.

:: Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment 'venv' not found.
    echo [INFO] Creating new virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment. Ensure Python 3.11+ is installed.
        pause
        exit /b 1
    )
    echo [INFO] Installing required dependencies...
    call venv\Scripts\activate.bat
    venv\Scripts\python.exe -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install requirements.
        pause
        exit /b 1
    )
) else (
    call venv\Scripts\activate.bat
)

:: Check if .env exists
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] Creating default .env from .env.example...
        copy .env.example .env >nul
    ) else (
        echo [WARNING] No .env or .env.example file found.
    )
)

echo [INFO] Virtual environment active.

:: Check if MONGODB_URI in .env still points to localhost or 127.0.0.1 (without printing the URI)
if exist ".env" (
    findstr /i /b "MONGODB_URI=" ".env" | findstr /i "localhost 127.0.0.1" >nul 2>&1
    if !errorlevel! equ 0 (
        echo [NOTICE] MONGODB_URI in .env is currently configured for local MongoDB ^(localhost / 127.0.0.1^).
        echo          To switch to MongoDB Atlas, update MONGODB_URI in .env and run check_atlas.bat.
        echo.
    )
)

echo [INFO] Starting StarPost Manager on http://127.0.0.1:8000 ...
echo.

:: Launch the application with virtual environment python
venv\Scripts\python.exe run.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server exited with status code %errorlevel%.
    pause
)
