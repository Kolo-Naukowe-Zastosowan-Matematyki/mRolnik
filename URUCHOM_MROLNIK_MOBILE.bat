@echo off
chcp 65001 >nul
setlocal
title mRolnik – telefon
echo =====================================================================
echo   mRolnik ma jeden interfejs dla komputera i telefonu.
echo =====================================================================
echo.
echo  Domyślnie serwer jest dostępny TYLKO z tego komputera (127.0.0.1).
echo  Na telefonie możesz też po prostu zmniejszyć okno przeglądarki – widok się dopasuje.
echo.
echo  Aby otworzyć mRolnik na telefonie w tej samej sieci Wi-Fi, ustaw w pliku .env:
echo      MROLNIK_HOST=0.0.0.0
echo      MROLNIK_AUTH_MODE=password
echo      MROLNIK_USERS=...   (wpis tworzy: .venv\Scripts\python -m src.security.passwords)
echo  i otwórz na telefonie adres http://ADRES-IP-KOMPUTERA:8000/web
echo.
echo  RYZYKO: każde urządzenie w tej sieci zobaczy stronę logowania, a bez HTTPS hasło
echo  i sesja płyną otwartym tekstem. Rób to tylko w zaufanej sieci domowej, nigdy w
echo  publicznym Wi-Fi (hotel, hackathon). Tryb dev bez logowania na 0.0.0.0 jest zablokowany.
echo.
pause
endlocal
call "%~dp0URUCHOM_MROLNIK.bat"
