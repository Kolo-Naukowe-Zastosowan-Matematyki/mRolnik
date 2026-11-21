"""Wspólne narzędzia testów strumienia B (atrapy HTTP, czyszczenie cache). Brak testów w tym pliku."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pytest
import requests
from PIL import Image
from pyproj import Transformer

_to2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


class FakeResp:
    def __init__(self, status: int = 200, payload: Any = None, text: Optional[str] = None, content: Optional[bytes] = None,
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.content = content if content is not None else self.text.encode("utf-8")
        self.encoding = "utf-8"
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


class FakeHTTP:
    """Atrapa requests.get/post: lista (fragment_url, metoda, handler(url, kwargs) -> FakeResp)."""

    def __init__(self) -> None:
        self.routes: List[Tuple[str, str, Callable[[str, Dict[str, Any]], FakeResp]]] = []
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []

    def add(self, fragment: str, handler: Callable[[str, Dict[str, Any]], FakeResp], method: str = "ANY") -> None:
        self.routes.append((fragment, method, handler))

    def _dispatch(self, method: str, url: str, **kw: Any) -> FakeResp:
        self.calls.append((method, url, kw))
        for frag, m, h in self.routes:
            if frag in url and m in ("ANY", method):
                return h(url, kw)
        raise requests.ConnectionError(f"brak sieci w teście: {url}")

    def get(self, url: str, **kw: Any) -> FakeResp:
        return self._dispatch("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> FakeResp:
        return self._dispatch("POST", url, **kw)

    def count(self, fragment: str) -> int:
        return sum(1 for _, u, _ in self.calls if fragment in u)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHTTP:
    fake = FakeHTTP()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: fake.get(url, **kw))
    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kw: fake.post(url, **kw))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return fake


@pytest.fixture(autouse=True)
def clean_state() -> None:
    from src.data import cadastre, climate, common, county, era5_archive, gus, history, ortho, reference, sentinel, soil, weather
    from src.hydromelior import hydrography
    for c in (cadastre._id_cache, cadastre._pt_cache, climate._climate_cache, sentinel._current_cache, sentinel._history_cache,
              soil._soil_cache,
              weather._forecast_cache, weather._synop_cache, weather._warn_cache, weather._hydro_cache, weather._hydro_hist_cache,
              hydrography._caps_cache, hydrography._wfs_cache, sentinel._climatology_cache, sentinel._true_color_cache,
              reference._hydro_back_cache,
              gus._gus_cache, history._indices_cache, history._index_month_cache, history._frame_cache, history._climate_cache,
              county._county_cache, county._mask_cache, county._result_cache, county._monthly_cache, ortho._cache):
        c.clear()
    era5_archive.reset()
    cadastre.reset_memory_index()
    sentinel.reset_token()
    common.reset_status()


def cdse_settings(monkeypatch: pytest.MonkeyPatch, configured: bool = True) -> None:
    import src.api_service as api_service
    from src.data import county, history, sentinel
    ns = SimpleNamespace(cdse_client_id="id" if configured else "", cdse_client_secret="sekret" if configured else "",
                         cdse_configured=configured, offline_demo=False, contact_email="")
    # każdy moduł, który czyta konfigurację CDSE, widzi te same ustawienia testowe
    for mod in (sentinel, history, county, api_service):
        monkeypatch.setattr(mod, "settings", ns)


# ---------------------------------------------------------------- dane przykładowe
def square_wkt_2180(lat: float, lon: float, d: float = 0.0015) -> str:
    pts = [(lon - d, lat - d), (lon + d, lat - d), (lon + d, lat + d), (lon - d, lat + d), (lon - d, lat - d)]
    xy = [_to2180.transform(x, y) for x, y in pts]
    return "POLYGON((" + ",".join(f"{x:.2f} {y:.2f}" for x, y in xy) + "))"


def uldk_text(lat: float, lon: float, pid: str = "302505_5.0014.114/1") -> str:
    return f"0\n{pid}|SRID=2180;{square_wkt_2180(lat, lon)}|wielkopolskie|średzki|Środa Wielkopolska|Śnieciska\n"


def synop_records() -> List[Dict[str, Any]]:
    return [
        {"id_stacji": "12330", "stacja": "Poznań", "data_pomiaru": "2026-11-15", "godzina_pomiaru": "11", "temperatura": "6.4",
         "predkosc_wiatru": "5", "kierunek_wiatru": "240", "wilgotnosc_wzgledna": "87.3", "suma_opadu": "0.2", "cisnienie": "1012.4"},
        {"id_stacji": "12375", "stacja": "Warszawa", "data_pomiaru": "2026-11-15", "godzina_pomiaru": "11", "temperatura": "5.1",
         "predkosc_wiatru": "3", "kierunek_wiatru": "200", "wilgotnosc_wzgledna": "90", "suma_opadu": "0", "cisnienie": "1010"},
        {"id_stacji": "12345", "stacja": "Koło", "data_pomiaru": "2026-11-15", "godzina_pomiaru": "11", "temperatura": None,
         "predkosc_wiatru": None, "kierunek_wiatru": None, "wilgotnosc_wzgledna": None, "suma_opadu": None, "cisnienie": None},
    ]


def open_meteo_forecast(days_past: int = 7, days_future: int = 14, rain: float = 1.0, et0: float = 0.6) -> Dict[str, Any]:
    from datetime import date, timedelta
    today = date(2026, 11, 15)
    dates = [(today + timedelta(days=i)).isoformat() for i in range(-days_past, days_future)]
    n = len(dates)
    return {
        "utc_offset_seconds": 3600,
        "current": {"time": "2026-11-15T12:45", "temperature_2m": 7.2, "relative_humidity_2m": 81, "wind_speed_10m": 3.4, "precipitation": 0.0},
        "daily": {"time": dates, "precipitation_sum": [rain] * n, "temperature_2m_max": [8.0] * n,
                  "temperature_2m_min": [1.0] * n, "et0_fao_evapotranspiration": [et0] * n},
    }


def open_meteo_soil_icon(hours: int = 8 * 24, end: str = "2026-11-15T12:00", start_val: float = 0.30, end_val: float = 0.34) -> Dict[str, Any]:
    from datetime import datetime, timedelta
    t_end = datetime.strptime(end, "%Y-%m-%dT%H:%M")
    times = [(t_end - timedelta(hours=hours - 1 - i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    vals = list(np.linspace(start_val, end_val, hours))
    return {"hourly": {"time": times, "soil_moisture_0_to_1cm": vals, "soil_moisture_1_to_3cm": vals,
                       "soil_moisture_3_to_9cm": vals, "soil_moisture_9_to_27cm": vals, "soil_temperature_6cm": [5.5] * hours}}


def ndvi_png(values: np.ndarray) -> bytes:
    """Koduje NDVI (NaN = brak) do formatu wyjściowego evalscriptu (UINT8: 0 brak, 1..255)."""
    arr = np.where(np.isfinite(values), 1 + np.round((np.clip(values, -1, 1) + 1) / 2 * 254), 0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()
