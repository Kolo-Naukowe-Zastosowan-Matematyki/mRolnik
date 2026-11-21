#!/usr/bin/env bash
# mRolnik – uruchomienie na macOS i Linuksie (odpowiednik mrolnik.bat dla Windows).
# Użycie (w Terminalu, w folderze mRolnik):  bash mrolnik.sh   [opcjonalnie: --port 8001]
set -euo pipefail
cd "$(dirname "$0")"

echo "====================================================================="
echo "                 mRolnik – asystent Twojego pola"
echo "====================================================================="
echo

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  PY=""
  for c in python3.13 python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "BŁĄD: potrzebny jest Python 3.12 lub nowszy."
    echo "  macOS: zainstaluj go ze strony https://www.python.org/downloads/"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    exit 1
  fi
  echo "[1/3] Tworzę lokalne środowisko Pythona w folderze .venv ($("$PY" --version)) ..."
  if ! "$PY" -m venv .venv; then
    rm -rf .venv
    echo
    echo "BŁĄD: nie udało się utworzyć środowiska .venv."
    echo "  Ubuntu/Debian: doinstaluj moduł venv poleceniem  sudo apt install python3-venv  i uruchom skrypt ponownie."
    exit 1
  fi
fi

if ! cmp -s requirements.txt .venv/requirements.zainstalowane.txt 2>/dev/null; then
  echo "[2/3] Instaluję biblioteki z requirements.txt (za pierwszym razem może to potrwać kilka minut) ..."
  if ! "$VENV_PY" -m pip install --disable-pip-version-check -q -r requirements.txt; then
    echo
    echo "BŁĄD: instalacja bibliotek nie powiodła się. Sprawdź połączenie z internetem i spróbuj ponownie."
    exit 1
  fi
  cp requirements.txt .venv/requirements.zainstalowane.txt
fi

echo "[3/3] Uruchamiam serwer mRolnik. Przeglądarka otworzy się automatycznie."
echo "      Adres strony pojawi się poniżej. Zakończenie pracy: Ctrl+C w tym oknie."
echo
exec "$VENV_PY" server.py --open "$@"
