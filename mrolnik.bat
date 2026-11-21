@echo off
chcp 65001 >nul
setlocal EnableExtensions
title mRolnik
cd /d "%~dp0"

echo =====================================================================
echo                  mRolnik – asystent Twojego pola
echo =====================================================================
echo.
rem Ustawienia (adres, tryb logowania, klucze) są w pliku .env – wzór: .env.example.
rem Domyślnie serwer słucha tylko na 127.0.0.1 (dostęp wyłącznie z tego komputera).

set "VENV_PY=.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :deps

echo [1/3] Tworzę lokalne środowisko Pythona w folderze .venv ...
py -3.12 -m venv .venv >nul 2>&1
if not exist "%VENV_PY%" python -m venv .venv >nul 2>&1
if not exist "%VENV_PY%" (
    echo.
    echo BŁĄD: nie znaleziono Pythona 3.12. Zainstaluj go ze strony https://www.python.org/downloads/
    echo i podczas instalacji zaznacz opcję "Add python.exe to PATH".
    pause
    exit /b 1
)

:deps
if not exist "requirements.txt" goto :run
fc /b "requirements.txt" ".venv\requirements.zainstalowane.txt" >nul 2>&1
if not errorlevel 1 goto :run
echo [2/3] Instaluję biblioteki z requirements.txt (za pierwszym razem może to potrwać kilka minut) ...
"%VENV_PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo BŁĄD: instalacja bibliotek nie powiodła się. Sprawdź połączenie z internetem i spróbuj ponownie.
    pause
    exit /b 1
)
copy /y "requirements.txt" ".venv\requirements.zainstalowane.txt" >nul

:run
echo [3/3] Uruchamiam serwer mRolnik. Przeglądarka otworzy się automatycznie.
echo       Jeśli mRolnik już działa, otworzy się tylko przeglądarka.
echo       Aby zakończyć pracę, zamknij to okno lub naciśnij CTRL+C.
echo.
"%VENV_PY%" server.py --open
if errorlevel 1 (
    echo.
    echo Serwer zakończył pracę z błędem – szczegóły powyżej.
    pause
)
endlocal
