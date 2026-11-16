"""Buduje działki przykładowych gospodarstw z PRAWDZIWYCH działek ewidencyjnych (GUGiK ULDK).

Uruchomienie (z folderu projektu, na komputerze z dostępem do uldk.gugik.gov.pl):
    python scripts/build_presets.py            → zapisuje src/data/presets_dzialki.json
    python scripts/build_presets.py --dry-run  → tylko raport, bez zapisu

Dla każdego gospodarstwa skrypt sprawdza punkty wokół punktu startowego (spirala co `step_m` metrów) i przyjmuje
działkę tylko, gdy:
  * ULDK zwraca działkę zawierającą punkt,
  * powierzchnia ≥ `min_ha` (odrzuca działki siedliskowe, drogi, rowy),
  * kształt nie jest wąskim pasem (zwartość 4πA/P² ≥ 0,05 – odrzuca drogi i rowy),
  * tej działki nie ma jeszcze w gospodarstwie.
Zapisywany jest punkt wewnątrz przyjętej działki i jej identyfikator TERYT, więc aplikacja wczytuje dokładnie tę działkę.
Granice, powierzchnia, NDVI i pogoda i tak są pobierane na żywo podczas analizy – plik zawiera tylko wybór działek
i przykładowe deklaracje użytkownika (uprawa, gleba), które nie opisują stanu łanu.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.data.cadastre import ParcelData, fetch_parcel_by_xy  # noqa: E402
from src.data.presets import FALLBACK_PRESETS, PRESETS_JSON  # noqa: E402

# Liczba działek celowo różna: duże gospodarstwa na zachodzie i północy, rozdrobnione na południowym wschodzie.
#   count – ile działek, step_m – odstęp punktów próbnych, min_ha – najmniejsza przyjmowana działka, crops – płodozmian
FARM_SPECS: Dict[str, Dict[str, Any]] = {
    "Wielkopolskie • Śnieciska (pow. średzki)": dict(count=14, step_m=220, min_ha=0.8, crops=[
        "Pszenica ozima", "Jęczmień jary", "Kukurydza na ziarno", "Rzepak ozimy", "Buraki cukrowe", "Pszenżyto ozime"]),
    "Kujawsko-Pomorskie • Kruszwica (pow. inowrocławski)": dict(count=12, step_m=300, min_ha=1.0, crops=[
        "Buraki cukrowe", "Rzepak ozimy", "Pszenica ozima", "Kukurydza na ziarno", "Jęczmień ozimy"]),
    "Lubelskie • Krasnystaw (pow. krasnostawski)": dict(count=6, step_m=150, min_ha=0.3, crops=[
        "Pszenica ozima", "Buraki cukrowe", "Rzepak ozimy", "Kukurydza na ziarno", "Soja"]),
    "Dolnośląskie • Strzelin (pow. strzeliński)": dict(count=9, step_m=300, min_ha=1.0, crops=[
        "Pszenica ozima", "Buraki cukrowe", "Rzepak ozimy", "Kukurydza na ziarno", "Jęczmień ozimy"]),
    "Lubuskie • Sulechów (pow. zielonogórski)": dict(count=3, step_m=300, min_ha=0.8, crops=[
        "Kukurydza na ziarno", "Żyto", "Pszenżyto ozime"]),
    "Łódzkie • Kutno (pow. kutnowski)": dict(count=5, step_m=180, min_ha=0.4, crops=[
        "Pszenica ozima", "Ziemniaki", "Kukurydza na ziarno", "Jęczmień jary"]),
    "Małopolskie • Proszowice (pow. proszowicki)": dict(count=4, step_m=120, min_ha=0.2, crops=[
        "Pszenica ozima", "Ziemniaki", "Kukurydza na ziarno", "Jęczmień jary"]),
    "Mazowieckie • Płońsk (pow. płoński)": dict(count=7, step_m=200, min_ha=0.5, crops=[
        "Pszenica ozima", "Rzepak ozimy", "Kukurydza na kiszonkę", "Pszenżyto ozime", "Owies"]),
    "Opolskie • Głubczyce (pow. głubczycki)": dict(count=11, step_m=300, min_ha=1.0, crops=[
        "Pszenica ozima", "Kukurydza na ziarno", "Rzepak ozimy", "Buraki cukrowe", "Soja"]),
    "Podkarpackie • Jarosław (pow. jarosławski)": dict(count=3, step_m=120, min_ha=0.2, crops=[
        "Pszenica ozima", "Rzepak ozimy", "Kukurydza na ziarno"]),
    "Podlaskie • Wysokie Mazowieckie (pow. wysokomazowiecki)": dict(count=8, step_m=220, min_ha=0.5, crops=[
        "Kukurydza na kiszonkę", "Trwały użytek zielony (łąka)", "Pszenżyto ozime", "Owies"]),
    "Pomorskie • Nowy Dwór Gdański (Żuławy Wiślane)": dict(count=16, step_m=350, min_ha=1.5, crops=[
        "Pszenica ozima", "Rzepak ozimy", "Buraki cukrowe", "Jęczmień ozimy", "Kukurydza na ziarno"]),
    "Śląskie • Racibórz (pow. raciborski)": dict(count=10, step_m=250, min_ha=0.8, crops=[
        "Pszenica ozima", "Buraki cukrowe", "Kukurydza na ziarno", "Rzepak ozimy", "Soja"]),
    "Świętokrzyskie • Opatów (pow. opatowski)": dict(count=4, step_m=120, min_ha=0.2, crops=[
        "Pszenica ozima", "Rzepak ozimy", "Buraki cukrowe", "Groch"]),
    "Warmińsko-Mazurskie • Kętrzyn (pow. kętrzyński)": dict(count=13, step_m=350, min_ha=1.5, crops=[
        "Rzepak ozimy", "Pszenica ozima", "Kukurydza na kiszonkę", "Trwały użytek zielony (łąka)", "Jęczmień ozimy"]),
    "Zachodniopomorskie • Pyrzyce (pow. pyrzycki)": dict(count=15, step_m=350, min_ha=1.5, crops=[
        "Pszenica ozima", "Buraki cukrowe", "Rzepak ozimy", "Kukurydza na ziarno", "Jęczmień ozimy"]),
}

MAX_RADIUS_M = 2500.0   # wszystkie działki w jednym rejonie pogodowym (≈5 km)
MIN_COMPACTNESS = 0.05  # 4πA/P²: koło = 1, kwadrat ≈ 0,79, pas 20×1000 m ≈ 0,06, droga 10×1000 m ≈ 0,03
PAUSE_S = 0.25          # odstęp między zapytaniami do ULDK


def offset(lat: float, lon: float, dx_m: float, dy_m: float) -> Tuple[float, float]:
    return (lat + dy_m / 111_320.0, lon + dx_m / (111_320.0 * math.cos(math.radians(lat))))


def spiral(lat: float, lon: float, step_m: float) -> Iterator[Tuple[float, float]]:
    """Punkty próbne: środek, potem pierścienie co step_m (8·k punktów na k-tym pierścieniu), aż do MAX_RADIUS_M."""
    yield lat, lon
    k = 1
    while k * step_m <= MAX_RADIUS_M:
        n = 8 * k
        for i in range(n):
            a = 2 * math.pi * i / n + (k % 2) * math.pi / n   # przesunięcie co drugi pierścień
            yield offset(lat, lon, k * step_m * math.cos(a), k * step_m * math.sin(a))
        k += 1


def compactness(p: ParcelData) -> float:
    g = p.polygon_2180
    return 4 * math.pi * g.area / (g.length ** 2) if g.length else 0.0


def parcel_number(parcel_id: str) -> str:
    return parcel_id.rsplit(".", 1)[-1]


def build_farm(name: str, spec: Dict[str, Any], log: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    base = FALLBACK_PRESETS[name]
    lat0, lon0 = float(base[0]["lat"]), float(base[0]["lon"])
    soils = [str(b["soil"]) for b in base]
    # najpierw dotychczasowe punkty z rejonu (gdy leżą blisko pierwszego), potem spirala
    cands: List[Tuple[float, float, Optional[Dict[str, Any]]]] = []
    for b in base:
        d = math.hypot((float(b["lat"]) - lat0) * 111_320, (float(b["lon"]) - lon0) * 111_320 * math.cos(math.radians(lat0)))
        if d <= MAX_RADIUS_M:
            cands.append((float(b["lat"]), float(b["lon"]), b))
    cands += [(la, lo, None) for la, lo in spiral(lat0, lon0, spec["step_m"])]

    out: List[Dict[str, Any]] = []
    seen: set = set()
    rejected = {"brak_dzialki": 0, "za_mala": 0, "waski_pas": 0, "powtorzona": 0}
    tried = 0
    for la, lo, src in cands:
        if len(out) >= spec["count"]:
            break
        tried += 1
        p = fetch_parcel_by_xy(lo, la)
        time.sleep(PAUSE_S)
        if p is None:
            rejected["brak_dzialki"] += 1
            continue
        if p.parcel_id in seen:
            rejected["powtorzona"] += 1
            continue
        seen.add(p.parcel_id)
        if p.area_ha < spec["min_ha"]:
            rejected["za_mala"] += 1
            continue
        if compactness(p) < MIN_COMPACTNESS:
            rejected["waski_pas"] += 1
            continue
        i = len(out)
        inside = p.polygon_wgs84.representative_point()   # punkt na pewno wewnątrz działki
        crop = str(src["crop"]) if src else spec["crops"][i % len(spec["crops"])]
        soil = str(src["soil"]) if src else soils[i % len(soils)]
        out.append({"id_tag": f"Dz. {parcel_number(p.parcel_id)}", "lat": round(inside.y, 6), "lon": round(inside.x, 6),
                    "crop": crop, "soil": soil, "parcel_id": p.parcel_id, "area_ha": round(p.area_ha, 2)})
    rep = {"chciane": spec["count"], "znalezione": len(out), "sprawdzone_punkty": tried, "odrzucone": rejected}
    log.append(f"{name}: {len(out)}/{spec['count']} działek (sprawdzono {tried} punktów, odrzucono {rejected})")
    return out, rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="tylko raport, bez zapisu pliku")
    ap.add_argument("--farm", action="append", help="tylko wybrane gospodarstwo (pełna nazwa); można powtórzyć")
    args = ap.parse_args()

    names = args.farm or list(FARM_SPECS)
    old: Dict[str, Any] = {}
    if os.path.exists(PRESETS_JSON):
        with open(PRESETS_JSON, encoding="utf-8") as f:
            old = json.load(f)
    farms: Dict[str, Any] = dict(old.get("farms", {}))
    report: Dict[str, Any] = dict(old.get("raport", {}))
    log: List[str] = []
    for name in names:
        if name not in FARM_SPECS:
            print(f"Nieznane gospodarstwo: {name}", file=sys.stderr)
            return 2
        t = time.monotonic()
        parcels, rep = build_farm(name, FARM_SPECS[name], log)
        rep["sekundy"] = round(time.monotonic() - t, 1)
        print(log[-1], flush=True)
        if not parcels:
            print(f"  ! ULDK nie zwróciło żadnej działki – zostawiam poprzednią listę dla: {name}", flush=True)
            continue
        farms[name] = parcels
        report[name] = rep

    doc = {
        "opis": "Działki przykładowych gospodarstw wybrane skryptem scripts/build_presets.py z GUGiK ULDK (GetParcelByXY). "
                "Uprawa i gleba to przykładowe deklaracje użytkownika; granice, NDVI i pogoda są pobierane na żywo.",
        "zrodlo": "https://uldk.gugik.gov.pl/",
        "wygenerowano": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "farms": farms,
        "raport": report,
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    tmp = PRESETS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PRESETS_JSON)
    total = sum(len(v) for v in farms.values())
    print(f"Zapisano {PRESETS_JSON}: {len(farms)} gospodarstw, {total} działek.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
