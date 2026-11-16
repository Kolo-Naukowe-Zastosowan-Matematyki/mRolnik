"""Buduje src/data/imgw_normals.json – normy klimatyczne 1991–2020 dla stacji synoptycznych IMGW-PIB.

Źródło: publiczne archiwum IMGW-PIB, dane miesięczne ze stacji synoptycznych
https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/miesieczne/synop/
(pliki zip: 1991_1995/1991_1995_m_s.zip, 1996_2000/1996_2000_m_s.zip, 2001/2001_m_s.zip … 2020/2020_m_s.zip).

Z plików s_m_d_*.csv (kodowanie cp1250, bez nagłówka, opis kolumn w s_m_d_format.txt) bierzemy:
  kol. 0 NSP (kod stacji), 1 POST (nazwa), 2 ROK, 3 MC,
  kol. 6 TMXS – średnia maksymalna temperatura powietrza [°C] (status w kol. 7),
  kol. 10 TMNS – średnia minimalna temperatura powietrza [°C] (status w kol. 11),
  kol. 16 SUMM – miesięczna suma opadów [mm] (status w kol. 17).
Status "8" = brak pomiaru (pomijamy), status "9" = brak zjawiska (dla opadu → 0 mm).

Norma = średnia arytmetyczna wartości miesięcznych z lat 1991–2020; liczymy ją tylko, gdy dostępnych jest
co najmniej MIN_YEARS lat (WMO zaleca ≥ 80% kompletności – 24 z 30 lat). Liczba lat jest zapisywana w pliku.

Uruchomienie (jednorazowo, wynik jest dołączony do repozytorium; serwer niczego nie pobiera w trakcie pracy):
    .venv\\Scripts\\python scripts\\build_imgw_normals.py
Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

BASE = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne/miesieczne/synop"
PERIOD = (1991, 2020)
MIN_YEARS = 24
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "imgw_normals.json"
UA = "mRolnik/0.3 (prototyp; skrypt norm klimatycznych)"


def archive_urls() -> List[str]:
    urls = [f"{BASE}/1991_1995/1991_1995_m_s.zip", f"{BASE}/1996_2000/1996_2000_m_s.zip"]
    urls += [f"{BASE}/{y}/{y}_m_s.zip" for y in range(2001, PERIOD[1] + 1)]
    return urls


def fnum(s: str) -> Optional[float]:
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def value(row: List[str], col: int, status_col: int, zero_if_absent: bool = False) -> Optional[float]:
    st = row[status_col].strip() if len(row) > status_col else ""
    if st == "8":
        return None
    if st == "9":
        return 0.0 if zero_if_absent else None
    return fnum(row[col]) if len(row) > col else None


def parse_s_m_d(text: str, acc: Dict[str, Dict[str, Dict[int, Dict[int, float]]]], names: Dict[str, tuple]) -> int:
    n = 0
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 18:
            continue
        code, name = row[0].strip(), row[1].strip()
        try:
            year, month = int(row[2]), int(row[3])
        except ValueError:
            continue
        if not (PERIOD[0] <= year <= PERIOD[1]) or not (1 <= month <= 12):
            continue
        for key, col, sc, zero in (("tmax_c", 6, 7, False), ("tmin_c", 10, 11, False), ("precip_mm", 16, 17, True)):
            v = value(row, col, sc, zero)
            if v is not None:
                acc[code][key].setdefault(month, {})[year] = v
        if code not in names or year >= names[code][1]:
            names[code] = (name, year)
        n += 1
    return n


def fetch(url: str) -> bytes:
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            print(f"  błąd ({exc}), ponawiam…", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Nie udało się pobrać {url}")


def build() -> Dict:
    acc: Dict[str, Dict[str, Dict[int, Dict[int, float]]]] = defaultdict(lambda: defaultdict(dict))
    names: Dict[str, tuple] = {}
    files = []
    for url in archive_urls():
        print("Pobieram", url)
        with zipfile.ZipFile(io.BytesIO(fetch(url))) as zf:
            for info in zf.infolist():
                base = info.filename.rsplit("/", 1)[-1].lower()
                if base.startswith("s_m_d") and base.endswith(".csv") and info.file_size < 20_000_000:
                    rows = parse_s_m_d(zf.read(info).decode("cp1250"), acc, names)
                    files.append({"url": url, "file": info.filename, "rows": rows})
    stations = []
    for code, data in sorted(acc.items()):
        st = {"id": code, "wmo_id": "12" + code[-3:] if len(code) == 9 else None, "name": names[code][0].title()}
        complete = False
        for key in ("precip_mm", "tmax_c", "tmin_c"):
            vals, years = [], []
            for m in range(1, 13):
                ys = data[key].get(m, {})
                if len(ys) >= MIN_YEARS:
                    vals.append(round(sum(ys.values()) / len(ys), 1))
                else:
                    vals.append(None)
                years.append(len(ys))
            st[key] = vals
            st[key.split("_")[0] + "_years"] = years
            complete = complete or any(v is not None for v in vals)
        if complete:
            stations.append(st)
        else:
            print(f"  pominięto {code} {st['name']}: za mało lat ({max(st['precip_years'])} lat opadu)")
    return {
        "period": f"{PERIOD[0]}–{PERIOD[1]}",
        "min_years": MIN_YEARS,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "IMGW-PIB – dane miesięczne ze stacji synoptycznych (archiwum danych publicznych)",
        "source_url": BASE + "/",
        "attribution": "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy",
        "method": ("Średnia arytmetyczna miesięcznych wartości TMXS (średnia temp. maks.), TMNS (średnia temp. min.) i SUMM "
                   f"(suma opadów) z lat {PERIOD[0]}–{PERIOD[1]}; norma tylko przy ≥{MIN_YEARS} latach danych. "
                   "Przetworzenie: mRolnik (scripts/build_imgw_normals.py)."),
        "files": len(files),
        "stations": stations,
    }


if __name__ == "__main__":
    result = build()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Zapisano {OUT} – {len(result['stations'])} stacji z {result['files']} plików.")
