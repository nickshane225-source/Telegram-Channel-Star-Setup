@echo off
setlocal enabledelayedexpansion
title StarPost Manager - MongoDB Atlas Diagnostics

chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run run.bat first to initialize venv.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
venv\Scripts\python.exe scripts\check_atlas.py %*
set EXIT_CODE=%errorlevel%

echo.
pause
exit /b %EXIT_CODE%
