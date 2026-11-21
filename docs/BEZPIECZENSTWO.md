# mRolnik – bezpieczeństwo

Dokument strumienia A (bezpieczeństwo). Opisuje model zagrożeń, poprawki po audycie, konfigurację,
drogę do logowania przez login.gov.pl / SSO eRolnika, uwagi RODO i ryzyka, które zostają.

## 1. Model zagrożeń (skrót)

| Kto / co | Zagrożenie | Główne zabezpieczenie |
|---|---|---|
| Ktoś w tej samej sieci (Wi-Fi hotelu, hackathonu) | pełny dostęp do API, odczyt cudzych działek | nasłuch tylko na `127.0.0.1`; tryb `dev` zablokowany poza loopback; logowanie w `password`/`oidc` |
| Obca strona otwarta w przeglądarce rolnika | CSRF (wywołanie POST w imieniu zalogowanego) | ciasteczko sesji `SameSite=Lax`, token double-submit `X-CSRF-Token`, sprawdzanie nagłówka `Origin`, CORS tylko z listy |
| Edytor danych OSM / autor pliku CSV | XSS przez nazwy miejscowości, pola upraw | sanityzacja nazw na serwerze, CSP bez `unsafe-inline`, frontend tylko `textContent` (strumień C) |
| Złośliwy lub nieostrożny klient | DoS (masowe analizy, ogromne body, spam wyszukiwarki) | limit żądań (token bucket per IP i per sesja), limit body 256 KB, limit działek w `/api/analyze` (B), cache z limitem wpisów |
| Ktoś z zapisem do katalogu cache | wykonanie kodu przy `pickle.load` | brak pickle: `TTLCache` (SQLite + JSON) |
| Zgadywanie haseł | przejęcie konta | scrypt, porównanie w stałym czasie, blokada z rosnącym czasem, limit `POST /auth/login` |
| Osadzanie w obcej ramce | clickjacking | CSP `frame-ancestors 'self' https://erolnik.gov.pl https://*.gov.pl` |
| Odczyt logów | wyciek danych osobowych | logi bez query stringu, bez haseł i tokenów; log dostępu uvicorna wyłączony |

Poza zakresem prototypu: kompromitacja serwera/systemu operacyjnego, ataki na dostawców danych (CDSE, IMGW, GUGiK),
izolacja danych między gospodarstwami w bazie (dziś brak trwałej bazy użytkowników – patrz sekcja 7).

## 2. Co naprawiono (audyt → poprawka)

| Ustalenie audytu | Waga | Poprawka | Gdzie |
|---|---|---|---|
| Brak uwierzytelnienia i autoryzacji, serwer na `0.0.0.0` | Wysoka | Domyślnie `127.0.0.1`; wymienne logowanie (`dev`/`password`/`oidc`, zaślepka `saml`); wszystkie `/api/*` poza `/api/health` za `Depends(require_user)`; role `rolnik`/`doradca`/`admin`; `dev` odmawia startu na adresie innym niż loopback | `server.py`, `src/security/` |
| Globalny stan zastawek, `toggle-gate` przyjmuje dowolny string | Wysoka | Endpoint za logowaniem i CSRF. Enum statusu i stan per użytkownik: **do zrobienia przez strumień B** w `routes_analysis.py` (użyć `user: User = Depends(require_user)` i `user.id` jako klucza stanu) | `server.py` / B |
| DOM XSS przez `display_name` z Photon/Nominatim | Wysoka | Serwer usuwa `<`, `>`, znaki sterujące i formatujące (np. U+202E), skraca do 200 znaków, odrzuca współrzędne spoza Polski; CSP blokuje skrypty inline; frontend wstawia tekst przez `textContent` (C) | `src/web/routes_search.py`, `middleware.py` |
| Stored/self-XSS z CSV i formularza | Wysoka | CSP `script-src 'self'` (bez inline i bez CDN) jako druga linia obrony; escapowanie w UI – strumień C | `middleware.py` / C |
| DoS: brak limitu działek i zasobów | Wysoka | Limit żądań per IP i per sesja (ostrzejszy dla `/api/analyze`, `/api/search-location`, `POST /auth/login`), 429 + `Retry-After`; limit body 256 KB (413, także dla `Transfer-Encoding: chunked`); maks. 50 działek – walidacja w routerze B | `ratelimit.py`, `middleware.py` |
| Wzrost dysku bez limitu (pickle wyszukiwarki) | Średnia | `TTLCache("geo_search", max_entries=5000, ttl=7 dni)`; puste wyniki nie są zapisywane; klucz to skrót SHA-256 frazy | `routes_search.py` |
| `pickle.load` z katalogu cache | Średnia | W wyszukiwarce brak pickle (pozostałe miejsca: strumień B) | `routes_search.py` |
| Wstrzyknięcie parametrów do zapytania ULDK | Średnia | Identyfikator działki musi pasować do wzorca TERYT (`^\d{6}_\d\.\d{4}(\.[A-Za-z0-9_-]+)?\.[0-9A-Za-z/-]+$`), więc nie zawiera `&`, `=`, `?`; urlencode w `fetch_parcel_by_id` – strumień B (`requests.get(..., params=...)`) | `routes_search.py` / B |
| Wyścigi i `except: pass` w cache | Średnia | `TTLCache` z blokadą; w wyszukiwarce każdy błąd sieci jest logowany | `routes_search.py` |
| Brak walidacji wejścia, 500 przy braku pola | Średnia | `q` 1–100 znaków (422), czyszczenie znaków sterujących; ogólne 422 po polsku bez echa danych; ogólne 500 po polsku z `error_id`, bez śladu stosu | `routes_search.py`, `bootstrap.py`, `middleware.py` |
| CDN bez SRI | Średnia | CSP pozwala tylko na skrypty i style z własnego źródła – biblioteki lokalnie w `static/vendor/` (C) | `middleware.py` / C |
| Brak nagłówków, CORS, rate limitu, logów audytowych | Niska | CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, HSTS w prod, `frame-ancestors` zamiast `X-Frame-Options`; CORS tylko z `MROLNIK_ALLOWED_ORIGINS`; log dostępu i audytu | `middleware.py`, `bootstrap.py` |
| `mrolnik.bat` (dawniej `URUCHOM_MROLNIK.bat`) zabija `taskkill /F` każdy proces na porcie 8000 | Niska | Brak `taskkill`. `server.py` sprawdza `/api/health`: gdy mRolnik już działa – tylko otwiera przeglądarkę; gdy port zajmuje inny program – wybiera następny wolny port. Własne `.venv` w folderze projektu zamiast venv innego projektu | `URUCHOM_*.bat`, `URUCHOM_MROLNIK.ps1`, `server.py` |
| Polityka Nominatim | – | Globalnie maks. 1 żądanie/s (blokada), identyfikowalny `User-Agent: mRolnik/2.1 (+kontakt)`, parametr `email`, cache 7 dni; Photon z limitem czasu 2,5 s | `routes_search.py` |
| Przestarzałe `on_event`, rozgrzewanie przy starcie | – | `lifespan`; rozgrzewanie tylko gdy `MROLNIK_WARMUP=1` | `server.py` |

## 3. Konfiguracja

Plik `.env` (wzór: `.env.example`, sekcja „A. Bezpieczeństwo”). Zmienne środowiskowe mają pierwszeństwo przed `.env`.

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MROLNIK_HOST` | `127.0.0.1` | Adres nasłuchu. `0.0.0.0` = cała sieć (głośne ostrzeżenie; tryb `dev` wtedy odmawia startu). |
| `MROLNIK_ENV` | `dev` | `prod` włącza HSTS, flagę `Secure`, wymaga klucza sesji i zabrania trybu `dev`. |
| `MROLNIK_AUTH_MODE` | `dev` | `dev` / `password` / `oidc` / `saml` (zaślepka – odmawia startu). |
| `MROLNIK_SECRET_KEY` | (brak) | Klucz HMAC sesji, min. 32 znaki w prod. Bez niego w dev generowany jest klucz tymczasowy. |
| `MROLNIK_ALLOWED_ORIGINS` | (puste) | Lista źródeł CORS (z ciasteczkami). Puste = tylko ta sama domena. `*` jest ignorowane. |
| `MROLNIK_SESSION_TTL_MIN` | `480` | Bezwzględny czas życia sesji (minuty). |
| `MROLNIK_COOKIE_SECURE` | `1` w prod | Flaga `Secure` ciasteczek (ustaw `1`, gdy jest HTTPS). |
| `MROLNIK_DEV_ROLE` | `admin` | Rola lokalnego użytkownika w trybie `dev`. |
| `MROLNIK_USERS` | (puste) | Konta trybu `password`: `login:rola:hash[,…]`. |
| `MROLNIK_OIDC_ISSUER` / `_DISCOVERY_URL` | | Adres dostawcy OIDC (discovery domyślnie `<issuer>/.well-known/openid-configuration`). |
| `MROLNIK_OIDC_CLIENT_ID` / `_CLIENT_SECRET` | | Dane klienta (sekret opcjonalny – klient publiczny z PKCE). |
| `MROLNIK_OIDC_REDIRECT_URI` | | Musi wskazywać `https://<host>/auth/callback` i być zarejestrowany u dostawcy. |
| `MROLNIK_OIDC_SCOPES` | `openid profile` | Zakresy. |
| `MROLNIK_OIDC_ROLE_CLAIM` / `_DEFAULT_ROLE` | `roles` / `rolnik` | Skąd brać rolę; brak/nieznana rola = rola domyślna. |
| `MROLNIK_RATE_LIMIT` | `1` | `0` wyłącza limit żądań (tylko testy). |
| `MROLNIK_MAX_BODY_KB` | `256` | Limit rozmiaru body. |
| `MROLNIK_CSP_IMG_HOSTS` | (puste) | Dodatkowe hosty `img-src` w CSP (np. kolejne usługi WMS), `https://host[:port]` po przecinku. Zawsze dozwolone: `mapy.geoportal.gov.pl`, `integracja.gugik.gov.pl`, `*.tile.openstreetmap.org`. |
| `MROLNIK_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Adresy reverse proxy, którym uvicorn ufa w `X-Forwarded-For/-Proto` (adres IP klienta dla limitów i logów). |
| `MROLNIK_WARMUP` | `0` | `1` = rozgrzewanie bufora analiz w tle przy starcie. |
| `MROLNIK_CONTACT_EMAIL` | (puste) | Kontakt w `User-Agent` dla Nominatim (wymagany przez ich politykę). |

Konto w trybie `password`:

```
.venv\Scripts\python -m src.security.passwords
```

Polecenie pyta o login, rolę i hasło (min. 12 znaków) i wypisuje linię do `MROLNIK_USERS`. Hasło jest
hashowane scryptem (N=2^14, r=8, p=1, sól 16 B). Po 5 nieudanych próbach (na login i na IP) logowanie jest
blokowane na 30 s, potem 60 s, 120 s… maks. 15 min.

Telefon w sieci domowej: `MROLNIK_HOST=0.0.0.0`, `MROLNIK_AUTH_MODE=password`, `MROLNIK_USERS=…`. Bez HTTPS hasło
i ciasteczko sesji płyną otwartym tekstem – tylko zaufana sieć. Do wdrożenia: reverse proxy z TLS (nginx/Caddy/IIS),
`MROLNIK_ENV=prod`, `MROLNIK_COOKIE_SECURE=1`.

### Kontrakt dla innych modułów

- `from src.security.auth import User, require_user, require_role` – `server.py` już dokłada `require_user` do
  routerów analizy, kalkulatorów i wyszukiwarki. Endpoint, który potrzebuje tożsamości (np. stan per użytkownik),
  dodaje `user: User = Depends(require_user)` i używa `user.id`. Rola: `Depends(require_role("doradca"))` (admin ma zawsze dostęp).
- Odpowiedzi błędów: 401 `{"detail","login_url"}`, 403 `{"detail"}`, 413 `{"detail"}`, 422 `{"detail","errors":[{"loc","msg","type"}]}`,
  429 `{"detail"}` + `Retry-After`, 500 `{"detail","error_id"}`.
- `GET /auth/me`, `GET|POST /auth/login`, `GET /auth/callback`, `POST /auth/logout` (wymaga `X-CSRF-Token`).

## 4. Sesje i CSRF

- `mrolnik_session`: podpisany HMAC-SHA256 (klucz z `MROLNIK_SECRET_KEY`, osobny klucz pochodny na każde zastosowanie),
  `HttpOnly`, `SameSite=Lax`, `Secure` w prod, zawiera tylko identyfikator sesji, id, nazwę i rolę użytkownika.
  Wylogowanie unieważnia identyfikator sesji (lista w pamięci do czasu wygaśnięcia).
- `mrolnik_csrf`: losowy token czytelny z JS; dla POST/PUT/PATCH/DELETE wymagany identyczny nagłówek `X-CSRF-Token`.
  Formularz logowania przesyła token w ukrytym polu. Token jest wymieniany po zalogowaniu i wylogowaniu.
  Dodatkowo żądanie z nagłówkiem `Origin` innym niż aplikacja i niż `MROLNIK_ALLOWED_ORIGINS` dostaje 403.

## 5. login.gov.pl i SSO eRolnika

### SSO eRolnika (lub inny dostawca OpenID Connect)

Tryb `oidc` jest gotowy: Authorization Code + PKCE (S256) + `state` + `nonce`, weryfikacja tokenu ID
(`iss`, `aud`/`azp`, `exp`, `iat`, `nonce`, podpis RS256 kluczami JWKS lub HS256 sekretem klienta).
Kroki:

1. Uzgodnić z operatorem eRolnika (KOWR/ARiMR – do potwierdzenia) rejestrację klienta OIDC: `client_id`, sekret
   (lub klient publiczny), adres zwrotny `https://<host-mrolnik>/auth/callback`, zakresy, nazwę atrybutu z rolą.
2. Ustawić zmienne `MROLNIK_OIDC_*` oraz `MROLNIK_AUTH_MODE=oidc`, `MROLNIK_ENV=prod`, `MROLNIK_SECRET_KEY`.
3. Jeśli mRolnik ma działać w ramce (`iframe`) w eRolniku, najlepiej hostować go w tej samej witrynie (np. subdomena
   `*.gov.pl`), bo ciasteczka `SameSite=Lax` nie są wysyłane w ramce z innej witryny. Alternatywa: osadzanie przez
   przekierowanie zamiast ramki.
4. Jeśli eRolnik przekazuje tożsamość inaczej (SAML, podpisany token w nagłówku z reverse proxy), dopisać dostawcę
   implementującego `AuthProvider` w `src/security/providers/` i zarejestrować go w `make_provider`.

### login.gov.pl (Węzeł Krajowy Identyfikacji Elektronicznej)

Węzeł Krajowy (profil zaufany, e-dowód, bankowość elektroniczna) udostępnia usługodawcom uwierzytelnienie
w protokole **SAML 2.0**, nie OIDC. W kodzie jest interfejs `AuthProvider` i zaślepka `SAMLProvider`
(`MROLNIK_AUTH_MODE=saml` odmawia startu z odesłaniem tutaj). Co jest potrzebne (do potwierdzenia
w aktualnej dokumentacji integracyjnej WK na gov.pl):

1. **Strona formalna:** integrację zgłasza podmiot publiczny będący usługodawcą (tu: KOWR lub operator eRolnika),
   nie wykonawca prototypu. Wymagane porozumienie/umowa z Ministrem Cyfryzacji (operator techniczny: COI)
   i wniosek o dostęp do środowiska testowego, a po testach – produkcyjnego.
2. **Klucze i certyfikaty:** para kluczy i certyfikat do podpisywania żądań `AuthnRequest`/`ArtifactResolve`
   oraz do deszyfrowania asercji (algorytmy wg specyfikacji WK). Klucz prywatny poza repozytorium (HSM lub
   magazyn sekretów), plan rotacji certyfikatów.
3. **Metadane:** metadane SP (entityID, adres Assertion Consumer Service `https://<host>/auth/saml/acs`) przekazane
   operatorowi; metadane IdP WK pobierane i weryfikowane po stronie mRolnik.
4. **Implementacja:** biblioteka z weryfikacją XML-DSig i obsługą szyfrowania (np. `python3-saml` + `xmlsec`
   lub `pysaml2`), wiązanie HTTP-Artifact z `ArtifactResolve` po SOAP, walidacja `InResponseTo`, czasu ważności,
   odbiorcy i podpisu; ochrona przed XML Signature Wrapping; wylogowanie (SLO).
5. **Dane:** WK zwraca m.in. imię, nazwisko, datę urodzenia i PESEL. Zgodnie z minimalizacją w sesji trzymać tylko
   pseudonimowy identyfikator (np. HMAC(PESEL) z osobnym kluczem), a role nadawać po stronie aplikacji
   (WK nie przekazuje ról: rola `doradca`/`spółka wodna` musi pochodzić z rejestru uprawnień lub ARiMR).
6. **Testy:** scenariusze testowe operatora WK, test penetracyjny przed produkcją.

## 6. RODO

- **Minimalizacja:** sesja zawiera tylko id/nazwę/rolę. Logi nie zawierają query stringu, haseł, tokenów ani treści żądań;
  zapisują metodę, ścieżkę, status, czas, adres IP i id użytkownika (cel: bezpieczeństwo, art. 6 ust. 1 lit. e/f).
  Nieznane loginy przy nieudanym logowaniu logowane są jako `<nieznany>` (żeby nie zapisać hasła wpisanego w pole loginu).
- **Retencja:** wyniki wyszukiwarki w cache 7 dni, maks. 5000 wpisów, klucz to skrót SHA-256 frazy; cache usuwa się,
  kasując `output/cache/mrolnik_cache.sqlite3`. Retencję logów ustala administrator (rekomendacja: 90 dni).
  Blokady logowania i unieważnione sesje są tylko w pamięci.
- **Odbiorcy danych (procesorzy/zewnętrzni administratorzy)** – zapytania z frazą lub współrzędnymi trafiają do:
  Komoot Photon (Niemcy), OpenStreetMap Foundation – Nominatim (Wielka Brytania; decyzja o adekwatności),
  GUGiK ULDK (Polska), oraz źródeł danych strumienia B (Copernicus Data Space Ecosystem, IMGW-PIB, Open-Meteo – Szwajcaria,
  GUGiK). Przed produkcją: umowy/podstawy przekazania, informacja dla użytkownika, rejestr czynności, DPIA.
  Rekomendacja: zastąpić Photon/Nominatim usługą GUGiK (np. wyszukiwarka PRG/PRNG) lub własną instancją.
- Numer działki z lokalizacją powiązany z rolnikiem to dane osobowe – po wprowadzeniu trwałej bazy gospodarstw
  wymagana izolacja danych per użytkownik i prawo do usunięcia.

## 7. Ryzyka, które zostają

- **Stan w pamięci procesu:** limity żądań, blokady logowania i lista wylogowanych sesji działają w jednym procesie.
  Przy kilku procesach/serwerach potrzebny wspólny magazyn (Redis) lub limity na reverse proxy.
- **Brak trwałej bazy użytkowników:** konta z env są dobre dla prototypu; produkcyjnie – SSO (OIDC/SAML) i rejestr ról.
- **Brak izolacji danych między gospodarstwami** w modułach B (np. stan zastawek) – wymaga zmian w `routes_analysis.py`.
- **HTTP bez TLS** przy `0.0.0.0` – TLS musi zapewnić reverse proxy.
- **Weryfikacja tokenu ID:** RS256 i HS256 weryfikowane podpisem; inne algorytmy (np. ES256) opierają się na
  bezpośrednim kanale TLS z token endpointem (dopuszczalne wg OIDC Core 3.1.3.7, ale słabsze). Dla pełnej weryfikacji
  ES/PS dodać bibliotekę `cryptography`/`PyJWT`.
- **SAML/login.gov.pl** – niezaimplementowane (sekcja 5).
- **CSP `img-src`** dopuszcza zewnętrzne kafle map – obrazy z tych hostów mogą ujawniać przybliżone położenie oglądanego
  obszaru ich operatorom.
- **Blokada logowania** może być użyta do chwilowego zablokowania cudzego konta (maks. 15 min) – świadomy kompromis.
- **Zależność od frontendu:** CSP i sanityzacja serwerowa nie zastąpią `textContent` w UI (strumień C).
