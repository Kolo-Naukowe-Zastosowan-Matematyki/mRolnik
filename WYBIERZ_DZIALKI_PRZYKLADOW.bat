@echo off
title mRolnik - wybor dzialek przykladowych gospodarstw (GUGiK ULDK)
cd /d "%~dp0"

echo =====================================================================
echo   mRolnik - wybor prawdziwych dzialek dla przykladowych gospodarstw
echo =====================================================================
echo.
echo Skrypt pyta GUGiK ULDK o dzialki wokol punktow startowych i zapisuje
echo src\data\presets_dzialki.json. Trwa to okolo 5-10 minut.
echo Potem uruchom ponownie URUCHOM_MROLNIK.bat.
echo.

set "PYTHON_EXE=..\research-nonlocal-dispersal\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" scripts\build_presets.py
echo.
pause
