<#
.SYNOPSIS
    Uruchamia mRolnik lokalnie w trybie produkcyjnym (hasło, 127.0.0.1) i wystawia go przez Cloudflare Tunnel (HTTPS).
.DESCRIPTION
    - Nie modyfikuje pliku .env (zwykłe mrolnik.bat dalej działa w trybie dev).
    - Dziedziczy CDSE_CLIENT_ID / CDSE_CLIENT_SECRET i pozostałe ustawienia źródeł z .env.
    - Przechowuje klucz sesji i konto demo w osobnym, ignorowanym przez Git pliku .env.tunnel.
    - Nasłuchuje na dedykowanym porcie loopback (domyślnie 8010), aby nie kolidować z lokalnym mrolnik.bat na 8000.
    - Wstrzymuje usypianie systemu Windows na czas działania tunelu (bez uprawnień administratora).
#>
param(
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$VenvPy = Join-Path $RootDir ".venv\Scripts\python.exe"
$TunnelEnvPath = Join-Path $RootDir ".env.tunnel"

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "         mRolnik - bezpieczny tunel HTTPS (Cloudflare Tunnel)" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $VenvPy)) {
    Write-Host "BŁĄD: nie znaleziono środowiska .venv." -ForegroundColor Red
    Write-Host "Uruchom najpierw raz mrolnik.bat (zainstaluje biblioteki), a potem uruchom mrolnik_tunnel.bat ponownie."
    exit 1
}

function Find-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\cloudflared.exe")
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

$Cloudflared = Find-Cloudflared
if (-not $Cloudflared) {
    Write-Host "Nie znaleziono programu cloudflared." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Zainstaluj go jednym poleceniem w PowerShell (darmowy, bez konta Cloudflare):"
    Write-Host "    winget install --id Cloudflare.cloudflared" -ForegroundColor Green
    Write-Host ""
    Write-Host "Po instalacji zamknij to okno i uruchom mrolnik_tunnel.bat ponownie."
    exit 1
}

function Initialize-TunnelEnv {
    Write-Host "[Konfiguracja wstępna] Tworzę lokalny plik .env.tunnel (ignorowany przez Git)." -ForegroundColor Cyan
    Write-Host "Zdalny dostęp przez tunel wymaga logowania hasłem (tryb dev działa tylko z tego komputera)."
    Write-Host ""

    $login = Read-Host "Login dla znajomych / jury (Enter = jury)"
    if ([string]::IsNullOrWhiteSpace($login)) { $login = "jury" }
    $login = $login.Trim()

    $role = Read-Host "Rola [rolnik/doradca/admin] (Enter = rolnik)"
    if ([string]::IsNullOrWhiteSpace($role)) { $role = "rolnik" }
    $role = $role.Trim()
    if ($role -notin @("rolnik", "doradca", "admin")) {
        Write-Host "BŁĄD: nieznana rola '$role' (dozwolone: rolnik, doradca, admin)." -ForegroundColor Red
        exit 1
    }

    while ($true) {
        $sec1 = Read-Host "Hasło (min. 12 znaków)" -AsSecureString
        $bstr1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec1)
        try { $pw1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr1) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr1) }

        if ($pw1.Length -lt 12) {
            Write-Host "Hasło musi mieć co najmniej 12 znaków. Spróbuj ponownie." -ForegroundColor Yellow
            continue
        }

        $sec2 = Read-Host "Powtórz hasło" -AsSecureString
        $bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec2)
        try { $pw2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr2) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2) }

        if ($pw1 -cne $pw2) {
            Write-Host "Hasła nie są zgodne. Spróbuj ponownie." -ForegroundColor Yellow
            continue
        }
        break
    }

    $secretKey = (& $VenvPy -c "import secrets; print(secrets.token_urlsafe(48))").Trim()

    # Kodujemy hasło w UTF-8 Base64 i przekazujemy przez env procesu potomnego (bez BOM z pipe w PowerShell 5.1 i bez hasła w argv)
    $pwB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pw1))
    $hpsi = [System.Diagnostics.ProcessStartInfo]::new()
    $hpsi.FileName = $VenvPy
    $hpsi.Arguments = '-c "import base64, os; from src.security.passwords import hash_password, verify_password; pw = base64.b64decode(os.environ[''MROLNIK_TMP_PW_B64'']).decode(''utf-8''); h = hash_password(pw); assert verify_password(pw, h); print(h)"'
    $hpsi.WorkingDirectory = $RootDir
    $hpsi.UseShellExecute = $false
    $hpsi.RedirectStandardOutput = $true
    $hpsi.RedirectStandardError = $true
    $hpsi.Environment["MROLNIK_TMP_PW_B64"] = $pwB64
    $hp = [System.Diagnostics.Process]::Start($hpsi)
    $pwHash = $hp.StandardOutput.ReadToEnd().Trim()
    $hpErr = $hp.StandardError.ReadToEnd().Trim()
    $hp.WaitForExit()
    if ($hp.ExitCode -ne 0 -or -not $secretKey -or -not $pwHash.StartsWith('scrypt$')) {
        Write-Host "BŁĄD: nie udało się wygenerować hasha hasła lub klucza sesji. $hpErr" -ForegroundColor Red
        exit 1
    }

    $lines = @(
        "# Lokalna konfiguracja tunelu Cloudflare dla mRolnik (ignorowana przez Git: .env.*)"
        "# Aby zmienić hasło, usuń ten plik i uruchom mrolnik_tunnel.bat ponownie."
        "MROLNIK_ENV=prod"
        "MROLNIK_HOST=127.0.0.1"
        "MROLNIK_AUTH_MODE=password"
        "MROLNIK_COOKIE_SECURE=1"
        "MROLNIK_FORWARDED_ALLOW_IPS=127.0.0.1"
        "MROLNIK_WARMUP=0"
        "MROLNIK_SECRET_KEY=$secretKey"
        "MROLNIK_USERS=${login}:${role}:${pwHash}"
    )
    [System.IO.File]::WriteAllLines($TunnelEnvPath, $lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host ""
    Write-Host "Zapisano konto '$login' (rola: $role) w .env.tunnel." -ForegroundColor Green
    Write-Host ""
}

if (-not (Test-Path $TunnelEnvPath)) {
    Initialize-TunnelEnv
}

# Wczytaj zmienne z .env.tunnel do procesu (nadpisują tryb dev z .env; klucze CDSE zostają z .env)
$LoadedVars = @{}
foreach ($raw in Get-Content $TunnelEnvPath -Encoding UTF8) {
    $line = $raw.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $idx = $line.IndexOf("=")
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    $LoadedVars[$k] = $v
}

function Test-PortFree([int]$p) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

$SelectedPort = $null
for ($p = $Port; $p -lt ($Port + 20); $p++) {
    if (Test-PortFree $p) { $SelectedPort = $p; break }
}
if (-not $SelectedPort) {
    Write-Host "BŁĄD: brak wolnego portu w zakresie $Port-$($Port + 19)." -ForegroundColor Red
    exit 1
}

# Wstrzymaj usypianie Windows na czas działania tunelu (bez uprawnień administratora)
$SleepGuardActive = $false
try {
    Add-Type -Namespace "MRolnikWin" -Name "Power" -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Auto, SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@ -ErrorAction Stop
    [void][MRolnikWin.Power]::SetThreadExecutionState(0x80000001) # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    $SleepGuardActive = $true
} catch {
    # Nie blokujemy uruchomienia, jeśli polityka ogranicza Add-Type
}

$ServerProc = $null
try {
    Write-Host "[1/2] Uruchamiam serwer mRolnik w trybie prod/password na 127.0.0.1:$SelectedPort ..." -ForegroundColor Cyan

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $VenvPy
    $psi.Arguments = "server.py --port $SelectedPort"
    $psi.WorkingDirectory = $RootDir
    $psi.UseShellExecute = $false
    $psi.Environment["MROLNIK_PORT"] = "$SelectedPort"
    foreach ($kv in $LoadedVars.GetEnumerator()) {
        $psi.Environment[$kv.Key] = $kv.Value
    }

    $ServerProc = [System.Diagnostics.Process]::Start($psi)

    $healthy = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($ServerProc.HasExited) { break }
        Start-Sleep -Milliseconds 350
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$SelectedPort/api/health" -TimeoutSec 2
            if ($resp.app -eq "mrolnik") { $healthy = $true; break }
        } catch {
            # serwer jeszcze wstaje
        }
    }

    if (-not $healthy) {
        Write-Host "BŁĄD: serwer mRolnik nie wystartował poprawnie (sprawdź output\logs\mrolnik.log)." -ForegroundColor Red
        exit 1
    }

    $loginHint = if ($LoadedVars["MROLNIK_USERS"]) { ($LoadedVars["MROLNIK_USERS"].Split(",")[0]).Split(":")[0] } else { "jury" }
    Write-Host "[2/2] Otwieram szyfrowany tunel HTTPS Cloudflare -> http://127.0.0.1:$SelectedPort" -ForegroundColor Green
    Write-Host "      Skopiuj adres https://....trycloudflare.com poniżej i wyślij znajomym / jury." -ForegroundColor Yellow
    Write-Host "      Login z .env.tunnel: $loginHint (hasło podane przy konfiguracji)." -ForegroundColor Yellow
    Write-Host "      Zakończenie pracy: Ctrl+C w tym oknie (zamyka tunel i serwer)." -ForegroundColor DarkGray
    Write-Host ""

    & $Cloudflared tunnel --url "http://127.0.0.1:$SelectedPort"
} finally {
    if ($ServerProc -and -not $ServerProc.HasExited) {
        Write-Host ""
        Write-Host "Zatrzymuję serwer mRolnik (PID $($ServerProc.Id)) ..." -ForegroundColor DarkGray
        try { Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if ($SleepGuardActive) {
        try { [void][MRolnikWin.Power]::SetThreadExecutionState(0x80000000) } catch {} # ES_CONTINUOUS (przywraca domyślne usypianie)
    }
}
