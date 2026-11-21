"""Data nalotu ortofotomapy GUGiK ze skorowidza (GetFeatureInfo, text/html)."""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import ortho
from src.web import routes_search
from tests.test_data_support import clean_state  # noqa: F401


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_search.router)
    return TestClient(app)

HTML_2025 = '''<html><script>
var skorDo5cm = [];
skorDo5cm.push({url:"https://opendata.geoportal.gov.pl/ortofotomapa/83847/83847_1550822_N-33-143-A-d-4-1.tif", godlo:"N-33-143-A-d-4-1", aktualnosc:"2025-05-25", wielkoscPiksela:"0.25", ukladWspolrzednych:"PL-1992", kolor:"RGB", aktualnoscRok:"2025"});
skorDo5cm.push({url:"https://opendata.geoportal.gov.pl/ortofotomapa/x.tif", godlo:"N-33-143-A-d-4-1", aktualnosc:"2025-06-02", wielkoscPiksela:"0.25", kolor:"CIR", aktualnoscRok:"2025"});
</script></html>'''


class _Resp:
    def __init__(self, text):
        self.text = text


def test_parse_and_pick_newest_rgb():
    sheets = ortho.parse_sheets(HTML_2025)
    assert len(sheets) == 2
    best = ortho.newest_rgb(sheets)
    assert best == {"date": "2025-05-25", "pixel_m": 0.25, "sheet": "N-33-143-A-d-4-1"}     # CIR pominięty
    assert ortho.newest_rgb([]) is None and ortho.parse_sheets("<html>brak</html>") == []


def test_layers_newest_first():
    assert ortho.layers_newest_first(date(2026, 11, 17)) == [
        "SkorowidzeOrtofotomapy2026", "SkorowidzeOrtofotomapy2025", "SkorowidzeOrtofotomapy2024", "SkorowidzeOrtofotomapyStarsze"]


def test_endpoint_queries_layers_until_found(client, monkeypatch):
    calls = []

    def fake(method, url, params=None, **kw):
        calls.append(params["LAYERS"])
        return _Resp(HTML_2025 if params["LAYERS"].endswith("2025") else "<html></html>")
    monkeypatch.setattr(ortho, "http_request", fake)
    monkeypatch.setattr(ortho, "utcnow", lambda: __import__("datetime").datetime(2026, 11, 17))
    r = client.get("/api/ortho/date", params={"lat": 52.31, "lon": 17.21}).json()
    assert r["date"] == "2025-05-25" and r["pixel_m"] == 0.25 and r["provenance"]["kind"] == "pomiar"
    assert calls == ["SkorowidzeOrtofotomapy2026", "SkorowidzeOrtofotomapy2025"]
    assert client.get("/api/ortho/date", params={"lat": 40.0, "lon": 17.2}).status_code == 422


def test_endpoint_no_guessing_on_error(client, monkeypatch):
    def boom(*a, **kw):
        raise ortho.SourceError("HTTP 503")
    monkeypatch.setattr(ortho, "http_request", boom)
    r = client.get("/api/ortho/date", params={"lat": 51.11, "lon": 19.31}).json()
    assert r["date"] is None and r["provenance"]["kind"] == "brak"
