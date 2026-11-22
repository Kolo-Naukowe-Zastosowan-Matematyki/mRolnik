@echo off
chcp 65001 >nul
setlocal EnableExtensions
title mRolnik – tunel HTTPS (Cloudflare)
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\tunnel.ps1" %*
if errorlevel 1 (
    echo.
    pause
)
endlocal
