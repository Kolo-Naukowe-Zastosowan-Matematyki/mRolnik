# Uruchamianie mRolnik z PowerShell (alternatywa dla URUCHOM_MROLNIK.bat).
# Jeśli system blokuje skrypty:  powershell -ExecutionPolicy Bypass -File .\URUCHOM_MROLNIK.ps1
Set-Location -Path $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$marker = Join-Path $PSScriptRoot ".venv\requirements.zainstalowane.txt"

Write-Host "=====================================================================" -ForegroundColor Green
Write-Host "                 mRolnik – asystent Twojego pola" -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Green

if (-not (Test-Path $venvPy)) {
    Write-Host "[1/3] Tworzę lokalne środowisko Pythona w folderze .venv ..."
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv .venv 2>$null }
    if (-not (Test-Path $venvPy)) { & python -m venv .venv 2>$null }
    if (-not (Test-Path $venvPy)) {
        Write-Host "BŁĄD: nie znaleziono Pythona 3.12. Zainstaluj go ze strony https://www.python.org/downloads/" -ForegroundColor Red
        Read-Host "Naciśnij Enter, aby zamknąć"
        exit 1
    }
}

if (Test-Path "requirements.txt") {
    $needInstall = -not (Test-Path $marker)
    if (-not $needInstall) {
        $needInstall = (Get-FileHash "requirements.txt").Hash -ne (Get-FileHash $marker).Hash
    }
    if ($needInstall) {
        Write-Host "[2/3] Instaluję biblioteki z requirements.txt (za pierwszym razem może to potrwać kilka minut) ..."
        & $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Host "BŁĄD: instalacja bibliotek nie powiodła się. Sprawdź połączenie z internetem." -ForegroundColor Red
            Read-Host "Naciśnij Enter, aby zamknąć"
            exit 1
        }
        Copy-Item "requirements.txt" $marker -Force
    }
}

Write-Host "[3/3] Uruchamiam serwer mRolnik (tylko 127.0.0.1, chyba że .env mówi inaczej). CTRL+C kończy pracę." -ForegroundColor Cyan
& $venvPy server.py --open
if ($LASTEXITCODE -ne 0) {
    Write-Host "Serwer zakończył pracę z błędem – szczegóły powyżej." -ForegroundColor Red
    Read-Host "Naciśnij Enter, aby zamknąć"
}
