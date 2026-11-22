# Udostępnianie mRolnik z własnego laptopa (Cloudflare Tunnel)

Najszybszy, **w 100% darmowy** sposób przetestowania aplikacji ze znajomymi lub pokazania jej jury **bez wrzucania kodu na zewnętrzne chmury** (prywatne repozytorium i plik `.env` zostają wyłącznie na Twoim komputerze).

## Jak to działa

- Skrypt `mrolnik_tunnel.bat` (`scripts/tunnel.ps1`) uruchamia `mRolnik` wyłącznie na `127.0.0.1:8010` (nie wystawia go w lokalnym Wi-Fi) w trybie **produkcyjnym z logowaniem hasłem** (`MROLNIK_ENV=prod`, `MROLNIK_AUTH_MODE=password`, `MROLNIK_COOKIE_SECURE=1`).
- Korzysta z Twojego istniejącego cache (`output/cache/`, m.in. BDOT10k i SQLite) oraz z kluczy `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` zapisanych w `.env`.
- Zwykłe `mrolnik.bat` **nadal działa bez zmian** w trybie `dev` na porcie `8000`.
- Na czas działania okna tunelu skrypt automatycznie wstrzymuje usypianie systemu Windows (bez uprawnień administratora), a po naciśnięciu `Ctrl+C` zamyka zarówno tunel, jak i serwer.

---

## Krok 1. Jednorazowa instalacja `cloudflared` (0 zł, bez konta)

W PowerShell:

```powershell
winget install --id Cloudflare.cloudflared
```

Po instalacji otwórz nowe okno terminala (lub uruchom od razu `mrolnik_tunnel.bat`).

---

## Krok 2. Uruchomienie tunelu

Kliknij dwukrotnie **`mrolnik_tunnel.bat`** (albo uruchom w PowerShell: `.\scripts\tunnel.ps1`).

1. **Przy pierwszym uruchomieniu** skrypt poprosi o login (np. `jury`), rolę (domyślnie `rolnik`) oraz hasło (min. 12 znaków) i zapisze je bezpiecznie w pliku **`.env.tunnel`** (ignorowanym przez Git na mocy reguły `.env.*` w `.gitignore`).
2. Po kilku sekundach `cloudflared` wypisze w konsoli publiczny adres HTTPS, np.:
   ```text
   https://example-random-words.trycloudflare.com
   ```
3. Wyślij znajomym lub jury ten link oraz ustalony login i hasło.

---

## Przydatne wskazówki

- **Zmiana hasła lub dodanie kolejnego konta:** usuń plik `.env.tunnel` i uruchom `mrolnik_tunnel.bat` ponownie (albo dopisz drugie konto po przecinku w `MROLNIK_USERS`, generując hash poleceniem `.venv\Scripts\python -m src.security.passwords`).
- **Zmiana adresu przy restarcie:** szybki tunel (`*.trycloudflare.com`) generuje nowy losowy adres po każdym restarcie. Aby utrzymać ten sam link dla znajomych/jury, nie zamykaj okna i trzymaj laptop podłączony do zasilania oraz stabilnego internetu (najlepiej po kablu lub pewnym Wi-Fi).
- **Własny stały adres (opcjonalnie, również za darmo):** jeśli masz domenę podpiętą pod darmowe konto Cloudflare, możesz utworzyć nazwany tunel (`cloudflared tunnel login` → `cloudflared tunnel create mrolnik` → `cloudflared tunnel route dns`) kierujący na `http://127.0.0.1:8010`.
- **Zakończenie sesji:** naciśnij `Ctrl+C` w oknie tunelu — natychmiast zamyka publiczny dostęp.
