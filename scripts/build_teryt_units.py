"""Buduje src/data/teryt_units.json – stałą listę województw, powiatów i gmin (TERYT) dla „Filtruj działki” (tryb EGiB).

Źródło: GUGiK – Państwowy Rejestr Granic (PRG), usługa WFS
    https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries
warstwy ms:A01_Granice_wojewodztw, ms:A02_Granice_powiatow, ms:A03_Granice_gmin; pola JPT_KOD_JE (TERYT) i JPT_NAZWA_
(bez geometrii – PROPERTYNAME). PRG to urzędowy, bieżący rejestr granic: bez jednostek historycznych, które zwraca GUS BDL.
Kod gminy w aplikacji: WWPPGG_R (jak w ULDK i w identyfikatorach działek); rodzaj R: 1 miejska, 2 wiejska, 3 miejsko-wiejska.

Uruchomienie (raz, gdy zmieni się podział administracyjny):  python scripts/build_teryt_units.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from typing import Dict, List, Optional, Tuple

import requests

WFS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "data", "teryt_units.json")
KIND = {"1": "gm. miejska", "2": "gm. wiejska", "3": "gm. miejsko-wiejska"}
LAYERS = {"woj": "ms:A01_Granice_wojewodztw", "powiat": "ms:A02_Granice_powiatow", "gmina": "ms:A03_Granice_gmin"}
_REC = re.compile(r"<ms:JPT_KOD_JE>([^<]*)</ms:JPT_KOD_JE>\s*<ms:JPT_NAZWA_>([^<]*)</ms:JPT_NAZWA_>")
_NEXT = re.compile(r'\bnext="([^"]+)"')


def fetch_layer(session: requests.Session, typename: str) -> List[Tuple[str, str]]:
    url: Optional[str] = WFS
    params: Optional[Dict[str, str]] = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": typename,
                                        "COUNT": "1000", "PROPERTYNAME": "JPT_KOD_JE,JPT_NAZWA_"}
    out: List[Tuple[str, str]] = []
    while url:
        for attempt in range(6):
            try:
                r = session.get(url, params=params, timeout=120)
                if r.status_code == 200 and "ExceptionReport" not in r.text[:500]:
                    break
            except requests.RequestException:
                pass
            time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"PRG WFS nie odpowiada ({typename})")
        r.encoding = "utf-8"
        out += [(k.strip(), unescape(n).strip()) for k, n in _REC.findall(r.text)]
        m = _NEXT.search(r.text[:4000])
        url, params = (unescape(m.group(1)).replace("http://", "https://", 1), None) if m else (None, None)
        time.sleep(0.5)
    return out


def build() -> Dict[str, object]:
    s = requests.Session()
    s.headers["User-Agent"] = "mRolnik/0.3 (prototyp; skrypt build_teryt_units)"
    woj = sorted({k: {"code": k, "name": n.lower()} for k, n in fetch_layer(s, LAYERS["woj"]) if re.fullmatch(r"\d{2}", k)}.values(),
                 key=lambda x: x["code"])
    pow_ = sorted({k: {"code": k, "woj": k[:2], "name": n if n.lower().startswith("powiat") else f"powiat {n}"}
                   for k, n in fetch_layer(s, LAYERS["powiat"]) if re.fullmatch(r"\d{4}", k)}.values(), key=lambda x: x["code"])
    gm = sorted({k: {"code": f"{k[:6]}_{k[6]}", "powiat": k[:4], "name": n, "kind": KIND.get(k[6], "")}
                 for k, n in fetch_layer(s, LAYERS["gmina"]) if re.fullmatch(r"\d{7}", k)}.values(), key=lambda x: x["code"])
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "source": "GUGiK – Państwowy Rejestr Granic (PRG), jednostki administracyjne",
        "source_url": WFS,
        "fetched_at": now,
        "note": "TERYT (JPT_KOD_JE) i nazwy z PRG; gmina miejsko-wiejska jako jedna pozycja (rodzaj 3).",
        "counts": {"woj": len(woj), "powiat": len(pow_), "gmina": len(gm)},
        "woj": woj, "powiat": pow_, "gmina": gm,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = build()
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(OUT + ".tmp", OUT)
    print(f"Zapisano {OUT}: {doc['counts']} (stan na {doc['fetched_at']})")


if __name__ == "__main__":
    main()
