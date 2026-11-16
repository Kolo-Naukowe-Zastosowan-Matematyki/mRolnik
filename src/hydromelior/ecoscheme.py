"""Informacja o ekoschematach związanych z wodą na TUZ – WYŁĄCZNIE INFORMACYJNIE.

Moduł nie stwierdza spełnienia warunków ekoschematu i nie tworzy dokumentów do wniosku. Pokazuje tylko fakty,
które da się sprawdzić w otwartych danych (ewidencja GUGiK, Sentinel-2, prognoza pogody); kryteria wymagające
danych ARiMR (rodzaj użytku w LPIS, okres utrzymania wody stwierdzony przez monitoring ARiMR) mają status
"brak danych". Warunki formalne weryfikuje wyłącznie ARiMR.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional

from src.data.cadastre import fetch_parcel_by_id, fetch_parcel_by_xy, parcel_provenance
from src.data.common import SourceError, iso_utc, missing, provenance
from src.data.sentinel import analyze_current
from src.data.weather import fetch_forecast, summarize_daily

log = logging.getLogger(__name__)

STATUSES = ("spełnione", "niespełnione", "brak danych")
DISCLAIMER = ("DOKUMENT INFORMACYJNY – nie stanowi dowodu ani wniosku do ARiMR. Zestawienie pokazuje wyłącznie dane dostępne "
              "publicznie i nie potwierdza spełnienia warunków żadnego ekoschematu. Aktualne warunki, terminy i stawki sprawdź "
              "w materiałach ARiMR; o przyznaniu płatności decyduje ARiMR.")
SCHEMES = [
    {"name": "Retencjonowanie wody na trwałych użytkach zielonych", "info_url": "https://www.gov.pl/web/arimr"},
    {"name": "Ekstensywne użytkowanie trwałych użytków zielonych", "info_url": "https://www.gov.pl/web/arimr"},
]
NDVI_COVER_MIN = 0.3


def _criterion(name: str, status: str, evidence: str, prov: Dict[str, Any]) -> Dict[str, Any]:
    assert status in STATUSES
    return {"name": name, "status": status, "evidence": evidence, "provenance": prov}


def ecoscheme_info(lat: float, lon: float, parcel_id: Optional[str] = None) -> Dict[str, Any]:
    criteria: List[Dict[str, Any]] = []
    parcel = fetch_parcel_by_id(parcel_id) if parcel_id else None
    if parcel is None:
        parcel = fetch_parcel_by_xy(lon, lat)
    p_prov = parcel_provenance(parcel)
    if parcel:
        criteria.append(_criterion("Działka odnaleziona w ewidencji gruntów", "spełnione",
                                   f"Działka {parcel.parcel_id}, obręb {parcel.region}, gm. {parcel.commune}; pow. geometryczna {parcel.area_ha:.2f} ha.",
                                   p_prov))
    else:
        criteria.append(_criterion("Działka odnaleziona w ewidencji gruntów", "brak danych",
                                   "Usługa GUGiK ULDK nie zwróciła działki dla wskazanego miejsca.", p_prov))

    criteria.append(_criterion(
        "Grunt zadeklarowany jako trwały użytek zielony (TUZ)", "brak danych",
        "Rodzaj użytku i deklaracja we wniosku są w systemie ARiMR (LPIS/IACS); mRolnik nie ma do nich dostępu.",
        missing("ARiMR – LPIS/IACS", "https://www.gov.pl/web/arimr", "Dane dostępne po zalogowaniu w systemach ARiMR.")))
    criteria.append(_criterion(
        "Woda utrzymywana na działce przez okres wymagany warunkami ekoschematu", "brak danych",
        "Okres utrzymania wody stwierdza ARiMR (m.in. monitoring satelitarny ARiMR); mRolnik nie ocenia tego warunku.",
        missing("ARiMR", "https://www.gov.pl/web/arimr", "Brak publicznego źródła danych dla tego warunku.")))

    if parcel:
        s2 = analyze_current(parcel)
        ndvi = s2.get("ndvi")
        if ndvi and ndvi.get("mean") is not None:
            ok = ndvi["mean"] >= NDVI_COVER_MIN
            criteria.append(_criterion(
                f"Na działce występuje okrywa roślinna (średnie NDVI ≥ {NDVI_COVER_MIN})", "spełnione" if ok else "niespełnione",
                f"Średnie NDVI {ndvi['mean']:.2f} ze zobrazowania z {ndvi.get('acquired_on')} ({ndvi.get('valid_pct')}% działki bez chmur).",
                s2["provenance"]))
        else:
            criteria.append(_criterion(f"Na działce występuje okrywa roślinna (średnie NDVI ≥ {NDVI_COVER_MIN})", "brak danych",
                                       s2["provenance"].get("note", "Brak NDVI."), s2["provenance"]))

    try:
        fc = fetch_forecast(lat, lon, "auto")
        bal = summarize_daily(fc["daily"])["climatic_water_balance_14d_mm"]
        f_prov = provenance("Open-Meteo – " + fc["model_label"], "https://open-meteo.com/", "prognoza", fetched_at=fc.get("fetched_at"),
                            note="Bilans = suma opadów − suma ET0 w horyzoncie prognozy. Dane: Open-Meteo.com, CC BY 4.0.")
        if bal is None:
            raise ValueError("brak bilansu")
        criteria.append(_criterion("Prognozowany bilans wodny 14 dni jest dodatni (sprzyja utrzymaniu wody)",
                                   "spełnione" if bal > 0 else "niespełnione", f"Bilans: {bal:+.1f} mm.", f_prov))
    except (SourceError, ValueError, KeyError) as exc:
        log.info("Ekoschemat – prognoza: %s", exc)
        criteria.append(_criterion("Prognozowany bilans wodny 14 dni jest dodatni (sprzyja utrzymaniu wody)", "brak danych",
                                   "Prognoza niedostępna.", missing("Open-Meteo", "https://open-meteo.com/", "Prognoza niedostępna.")))

    summary = ("Na podstawie danych publicznych NIE da się potwierdzić spełnienia warunków ekoschematu – kluczowe warunki "
               "(rodzaj użytku, okres utrzymania wody) weryfikuje ARiMR. Powyższe fakty mają charakter pomocniczy.")
    return {
        "informational": True,
        "disclaimer": DISCLAIMER,
        "generated_at": iso_utc(),
        "parcel_id": parcel.parcel_id if parcel else None,
        "schemes": SCHEMES,
        "criteria": criteria,
        "summary": summary,
    }


def render_html(info: Dict[str, Any]) -> str:
    """HTML do pobrania; wszystkie wartości escapowane."""
    e = lambda v: html.escape("" if v is None else str(v), quote=True)  # noqa: E731
    rows = "".join(
        f"<tr><td>{e(c['name'])}</td><td>{e(c['status'])}</td><td>{e(c['evidence'])}</td>"
        f"<td>{e(c['provenance'].get('source'))} ({e(c['provenance'].get('kind'))})</td></tr>"
        for c in info["criteria"]
    )
    schemes = "".join(f"<li>{e(s['name'])}</li>" for s in info["schemes"])
    return f"""<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"><title>Dokument informacyjny – ekoschematy (mRolnik)</title>
<style>body{{font-family:Arial,sans-serif;color:#1b1b1b;max-width:960px;margin:24px auto;padding:0 16px;line-height:1.5}}
.banner{{border:3px solid #b00020;padding:12px 16px;font-weight:bold;font-size:1.1em}}table{{border-collapse:collapse;width:100%;margin:16px 0}}
th,td{{border:1px solid #999;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eee}}</style></head>
<body>
<h1>DOKUMENT INFORMACYJNY – nie stanowi dowodu ani wniosku do ARiMR</h1>
<p class="banner">{e(info['disclaimer'])}</p>
<p>Wygenerowano: {e(info['generated_at'])} &middot; Działka: {e(info.get('parcel_id') or 'nie ustalono')}</p>
<p>Ekoschematy, których dotyczy zestawienie (informacyjnie):</p><ul>{schemes}</ul>
<table><thead><tr><th>Fakt / warunek</th><th>Status</th><th>Dane</th><th>Źródło</th></tr></thead><tbody>{rows}</tbody></table>
<p><strong>Podsumowanie:</strong> {e(info['summary'])}</p>
</body></html>"""
