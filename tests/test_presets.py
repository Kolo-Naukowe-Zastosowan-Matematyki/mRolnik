"""Przykładowe gospodarstwa: wczytywanie działek zweryfikowanych w ULDK i skrypt scripts/build_presets.py."""
from __future__ import annotations

import json

from shapely.geometry import box

from scripts import build_presets as bp
from src.data import presets
from src.data.cadastre import ParcelData

FARM = presets.DEFAULT_FARM


def test_fallback_when_no_file(tmp_path):
    out = presets.build_presets(str(tmp_path / "brak.json"))
    assert set(out) == set(presets.FALLBACK_PRESETS)
    assert out[FARM] == presets.FALLBACK_PRESETS[FARM]


def test_verified_file_replaces_known_farms_only(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"farms": {
        FARM: [{"id_tag": "Dz. 7", "lat": 52.2, "lon": 17.2, "crop": "Soja", "soil": "x", "parcel_id": "302505_5.0014.7",
                "area_ha": 3.1}, {"lat": 99, "lon": 17.2}],
        "Nieznane • gospodarstwo": [{"lat": 52.0, "lon": 19.0}]}}), encoding="utf-8")
    out = presets.build_presets(str(f))
    assert out[FARM] == [{"id_tag": "Dz. 7", "lat": 52.2, "lon": 17.2, "crop": "Soja", "soil": "x", "parcel_id": "302505_5.0014.7"}]
    assert "Nieznane • gospodarstwo" not in out
    other = next(n for n in presets.FALLBACK_PRESETS if n != FARM)
    assert out[other] == presets.FALLBACK_PRESETS[other]


def test_broken_file_falls_back(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{nie json", encoding="utf-8")
    assert presets.build_presets(str(f))[FARM] == presets.FALLBACK_PRESETS[FARM]


def test_spec_counts_differ_and_cover_all_farms():
    assert set(bp.FARM_SPECS) == set(presets.FALLBACK_PRESETS)
    counts = [s["count"] for s in bp.FARM_SPECS.values()]
    assert max(counts) >= 15 and min(counts) <= 4 and len(set(counts)) >= 8


def _parcel(pid: str, x0: float, y0: float, w: float, h: float) -> ParcelData:
    g = box(x0, y0, x0 + w, y0 + h)               # w metrach (udajemy EPSG:2180)
    wgs = box(17.2, 52.2, 17.201, 52.201)
    return ParcelData(pid, "wielkopolskie", "średzki", "Środa", "Śnieciska", g.area / 10_000, 52.2005, 17.2005, wgs, g)


def test_build_farm_filters_small_narrow_and_duplicate(monkeypatch):
    seq = iter([
        _parcel("A.1", 0, 0, 200, 200),            # 4 ha – OK (pierwszy punkt startowy)
        _parcel("A.1", 0, 0, 200, 200),            # powtórzona
        None,                                      # brak działki
        _parcel("A.2", 0, 0, 30, 30),              # 0,09 ha – za mała
        _parcel("A.3", 0, 0, 8, 2000),             # 1,6 ha, ale droga/rów – wąski pas
        _parcel("A.4", 0, 0, 150, 120),            # OK
    ] + [_parcel(f"B.{i}", 0, 0, 120, 120) for i in range(40)])
    monkeypatch.setattr(bp, "fetch_parcel_by_xy", lambda lon, lat: next(seq))
    monkeypatch.setattr(bp, "PAUSE_S", 0)
    spec = dict(count=5, step_m=200, min_ha=0.8, crops=["Pszenica ozima", "Rzepak ozimy"])
    out, rep = bp.build_farm(FARM, spec, [])
    assert [o["parcel_id"] for o in out] == ["A.1", "A.4", "B.0", "B.1", "B.2"]
    assert rep["odrzucone"] == {"brak_dzialki": 1, "za_mala": 1, "waski_pas": 1, "powtorzona": 1}
    assert out[0]["id_tag"] == "Dz. 1" and out[0]["crop"] == presets.FALLBACK_PRESETS[FARM][0]["crop"]
    assert all(52.2 <= o["lat"] <= 52.201 for o in out)   # punkt wewnątrz działki


def test_spiral_stays_in_weather_cell():
    pts = list(bp.spiral(52.0, 17.0, 300))
    assert pts[0] == (52.0, 17.0) and len(pts) == 1 + sum(8 * k for k in range(1, 9))
    assert max(abs(la - 52.0) for la, _ in pts) * 111_320 <= bp.MAX_RADIUS_M + 1
