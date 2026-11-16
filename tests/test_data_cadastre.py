"""GUGiK ULDK: parsowanie, urlencode, cache punkt-w-wielokącie, brak fikcyjnych działek."""
from __future__ import annotations

import numpy as np
import pytest
import requests
import shapely

from src.data import cadastre
from tests.test_data_support import FakeResp, clean_state, http, uldk_text  # noqa: F401


def test_parse_real_format():
    p = cadastre.parse_uldk_response(uldk_text(52.2020, 17.2012))
    assert p.parcel_id == "302505_5.0014.114/1"
    assert (p.voivodeship, p.county, p.commune, p.region) == ("wielkopolskie", "średzki", "Środa Wielkopolska", "Śnieciska")
    assert p.centroid_lat == pytest.approx(52.2020, abs=1e-4) and p.centroid_lon == pytest.approx(17.2012, abs=1e-4)
    assert 5 < p.area_ha < 7  # kwadrat ok. 0,003° × 0,003°
    coords = p.exterior_coords_lonlat()
    assert coords[0] == coords[-1] and len(coords) == 5


def test_parse_error_status():
    assert cadastre.parse_uldk_response("-1 brak wyników\n") is None
    with pytest.raises(ValueError):
        cadastre.parse_uldk_response("")


def test_fetch_by_xy_urlencoded_and_cached_by_polygon(http):
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk_text(52.2020, 17.2012)))
    p = cadastre.fetch_parcel_by_xy(17.2012, 52.2020)
    assert p is not None
    method, url, kw = http.calls[0]
    assert url == "https://uldk.gugik.gov.pl/" and kw["params"]["request"] == "GetParcelByXY"
    assert kw["params"]["xy"] == "17.201200,52.202000,4326" and kw["params"]["srid"] == "2180"
    assert kw["timeout"] == 5.0
    # inny punkt w tej samej działce → z cache (wielokąt zawiera punkt), bez zapytania
    p2 = cadastre.fetch_parcel_by_xy(17.2020, 52.2025)
    assert p2.parcel_id == p.parcel_id and http.count("uldk") == 1
    # punkt poza działką (ok. 300 m dalej) → nowe zapytanie, nie "najbliższa z promienia"
    cadastre.fetch_parcel_by_xy(17.2060, 52.2020)
    assert http.count("uldk") == 2


def test_fetch_by_id_validates_and_encodes(http):
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=uldk_text(52.2020, 17.2012)))
    assert cadastre.fetch_parcel_by_id("302505_5.0014.114/1&request=GetX") is None
    assert http.calls == []
    p = cadastre.fetch_parcel_by_id("302505_5.0014.114/1")
    assert p.parcel_id == "302505_5.0014.114/1"
    assert http.calls[0][2]["params"]["id"] == "302505_5.0014.114/1"
    cadastre.reset_memory_index()
    assert cadastre.fetch_parcel_by_id("302505_5.0014.114/1") is not None and http.count("uldk") == 1  # cache 30 dni


def test_uldk_failure_never_creates_synthetic_parcel(http):
    def boom(u, kw):
        raise requests.Timeout("timeout")
    http.add("uldk.gugik.gov.pl", boom)
    assert cadastre.fetch_parcel_by_xy(17.2, 52.2) is None
    assert http.count("uldk") == 2  # 1 ponowienie
    assert not hasattr(cadastre, "create_synthetic_parcel")
    prov = cadastre.parcel_provenance(None)
    assert prov["kind"] == "brak"


def test_vectorized_mask():
    poly = shapely.box(0, 0, 10, 5)
    m = cadastre.parcel_mask(poly, np.linspace(-1, 11, 13), np.linspace(-1, 6, 8))
    assert m.shape == (8, 13) and m.sum() == 9 * 4
