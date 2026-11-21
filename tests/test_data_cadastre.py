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


def _egib_wfs_xml(lat: float, lon: float, pid: str = "302505_5.0014.114/1", gmina: str = "Zaniemyśl", obreb: str = "Śnieciska") -> str:
    x, y = cadastre.transformer_to_2180.transform(lon, lat)
    # urn:ogc:def:crs:EPSG::2180 ma kolejność osi (N, E), czyli (y, x)
    d = 100.0
    ring = [(y - d, x - d), (y - d, x + d), (y + d, x + d), (y + d, x - d), (y - d, x - d)]
    pos = " ".join(f"{n:.2f} {e:.2f}" for n, e in ring)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:wfs="http://www.opengis.net/wfs/2.0">'
        "<wfs:member>"
        f'<ms:dzialki gml:id="dzialki.{pid}">'
        "<ms:geom><gml:Polygon><gml:exterior><gml:LinearRing>"
        f'<gml:posList srsDimension="2">{pos}</gml:posList>'
        "</gml:LinearRing></gml:exterior></gml:Polygon></ms:geom>"
        f"<ms:ID_DZIALKI>{pid}</ms:ID_DZIALKI>"
        f"<ms:NAZWA_GMINY>{gmina}</ms:NAZWA_GMINY>"
        f"<ms:NAZWA_OBREBU>{obreb}</ms:NAZWA_OBREBU>"
        "</ms:dzialki>"
        "</wfs:member>"
        "</wfs:FeatureCollection>"
    )


def test_egib_wfs_fallback_on_malformed_uldk_by_xy_and_cooldown(http):
    tarpit = "0\n0101000020E6100000000000000080564000000000008056400101010101\n"
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=tarpit))
    http.add("PZGIK/EGIB/WFS/UslugaZbiorcza", lambda u, kw: FakeResp(200, text=_egib_wfs_xml(52.2020, 17.2012)))
    p = cadastre.fetch_parcel_by_xy(17.2012, 52.2020)
    assert p is not None
    assert p.parcel_id == "302505_5.0014.114/1"
    assert p.source == "egib_wfs"
    assert p.voivodeship == "wielkopolskie"
    assert p.county == "średzki"
    assert p.commune == "Zaniemyśl"
    assert p.region == "Śnieciska"
    assert p.area_ha == pytest.approx(4.0, abs=0.05)
    assert len(p.exterior_coords_lonlat()) == 5
    prov = cadastre.parcel_provenance(p)
    assert prov["kind"] == "pomiar" and "EGiB WFS" in prov["source"]
    assert http.count("uldk.gugik.gov.pl") == 1
    assert http.count("UslugaZbiorcza") == 1

    # Kolejne zapytanie dla innego punktu w okresie cooldownu omija uszkodzony ULDK i od razu pyta EGiB WFS
    http.routes.clear()
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=tarpit))
    http.add("PZGIK/EGIB/WFS/UslugaZbiorcza", lambda u, kw: FakeResp(200, text=_egib_wfs_xml(52.3230, 17.1670, pid="302106_2.0003.9/9", gmina="Kleszczewo", obreb="Kleszczewo")))
    p2 = cadastre.fetch_parcel_by_xy(17.1670, 52.3230)
    assert p2 is not None and p2.parcel_id == "302106_2.0003.9/9"
    assert http.count("uldk.gugik.gov.pl") == 1  # nie wywołano ponownie ULDK
    assert http.count("UslugaZbiorcza") == 2


def test_egib_wfs_fallback_by_id(http):
    tarpit = "0\n0101000020E6100000000000000080564000000000008056400101010101\n"
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text=tarpit))
    http.add("PZGIK/EGIB/WFS/UslugaZbiorcza", lambda u, kw: FakeResp(200, text=_egib_wfs_xml(52.2020, 17.2012)))
    p = cadastre.fetch_parcel_by_id("302505_5.0014.114/1")
    assert p is not None and p.parcel_id == "302505_5.0014.114/1" and p.source == "egib_wfs"
    wfs_calls = [kw for _, u, kw in http.calls if "UslugaZbiorcza" in u]
    assert len(wfs_calls) == 1 and "ms:ID_DZIALKI" in wfs_calls[0]["params"]["FILTER"]


def test_probe_uldk_rejects_malformed_body(http):
    from src.data.common import SourceError
    http.add("uldk.gugik.gov.pl", lambda u, kw: FakeResp(200, text="0\n0101000020E61000000000000000805640\n"))
    with pytest.raises(SourceError):
        cadastre.probe_uldk()
