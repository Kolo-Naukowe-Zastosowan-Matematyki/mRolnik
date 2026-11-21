"""Zestawy działek użytkownika (src/data/user_sets.py, /api/user-sets) i działka w punkcie (/api/parcel-at) – bez sieci."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.data import user_sets
from src.security.auth import require_user
from src.security.providers.base import User

P1 = {"lat": 52.2021, "lon": 17.1977, "parcel_id": "302505_5.0014.111/5", "id_tag": "Dz. 111/5"}
P2 = {"lat": 52.21, "lon": 17.2}


@pytest.fixture(autouse=True)
def tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(user_sets, "DIR", tmp_path / "user_sets")


def test_demo_sets_on_first_use_from_real_presets():
    sets = user_sets.list_sets("u1")
    assert [s["name"] for s in sets] == ["Pola przy Wiśle", "Działki pod kukurydzę", "Łąki przy rowach"]
    assert all(s["demo"] and s["parcels"] for s in sets)
    from src.data.presets import FARM_PRESETS
    real = {p.get("parcel_id") for ps in FARM_PRESETS.values() for p in ps}
    assert all(p.get("parcel_id") in real for s in sets for p in s["parcels"])   # tylko prawdziwe działki z presetów
    assert user_sets.list_sets("u1") == sets                                     # drugi odczyt z pliku, bez nowych DEMO


def test_create_rename_add_remove_delete():
    s = user_sets.create_set("u1", "  Pola  nad rowem ", [P1, P1, P2])
    assert s["name"] == "Pola nad rowem" and len(s["parcels"]) == 2                # spacje i duplikaty
    s = user_sets.update_set("u1", s["id"], name="Pola przy rowie", add=[P2, {"lat": 52.3, "lon": 17.3}])
    assert s["name"] == "Pola przy rowie" and len(s["parcels"]) == 3
    s = user_sets.update_set("u1", s["id"], remove=["302505_5.0014.111/5"])
    assert len(s["parcels"]) == 2
    user_sets.delete_set("u1", s["id"])
    assert all(x["id"] != s["id"] for x in user_sets.list_sets("u1"))
    with pytest.raises(KeyError):
        user_sets.delete_set("u1", s["id"])


def test_validation_and_limits(monkeypatch):
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "   ", [P1])
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "x" * 81, [P1])
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "Poza Polską", [{"lat": 48.0, "lon": 17.0}])
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "Zły numer", [{"lat": 52.2, "lon": 17.2, "parcel_id": "<script>"}])
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "pola przy wiśle", [P1])                       # nazwa zajęta (bez wielkości liter)
    monkeypatch.setattr(user_sets, "MAX_PARCELS_PER_SET", 2)
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "Za dużo", [P1, P2, {"lat": 52.3, "lon": 17.3}])
    monkeypatch.setattr(user_sets, "MAX_SETS", 4)
    user_sets.create_set("u1", "Czwarty", [P1])
    with pytest.raises(user_sets.SetError):
        user_sets.create_set("u1", "Piąty", [P1])


def test_users_are_separate():
    user_sets.create_set("anna", "Moje", [P1])
    assert "Moje" not in [s["name"] for s in user_sets.list_sets("jan")]
    assert user_sets._path("../../etc") .parent == user_sets.DIR                  # nazwa pliku z hasha – bez ścieżek


@pytest.fixture
def client():
    from src.web import routes_sets
    app = FastAPI()
    app.include_router(routes_sets.router)
    app.dependency_overrides[require_user] = lambda: User("lokalny", "Użytkownik lokalny", "user")
    return TestClient(app)


def test_api_roundtrip(client):
    r = client.get("/api/user-sets")
    assert r.status_code == 200 and len(r.json()["sets"]) == 3 and r.json()["limits"]["sets"] == user_sets.MAX_SETS
    s = client.post("/api/user-sets", json={"name": "Wynik filtra 20.11", "parcels": [P1, P2]}).json()["set"]
    assert len(s["parcels"]) == 2
    s2 = client.patch(f"/api/user-sets/{s['id']}", json={"name": "Działki nad Wartą", "add": [{"lat": 52.3, "lon": 17.3}]}).json()["set"]
    assert s2["name"] == "Działki nad Wartą" and len(s2["parcels"]) == 3
    assert client.post("/api/user-sets", json={"name": "", "parcels": []}).status_code == 422
    assert client.post("/api/user-sets", json={"name": "Działki nad Wartą", "parcels": []}).status_code == 422
    assert client.patch("/api/user-sets/zzzzzzzzzz", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/user-sets/{s['id']}").status_code == 200
    assert client.delete(f"/api/user-sets/{s['id']}").status_code == 404


def test_parcel_at(monkeypatch):
    from src.data import cadastre
    from src.data.cadastre import _build_parcel
    from src.web import routes_search
    from shapely.geometry import box
    p = _build_parcel("302505_5.0014.111/5", box(380000, 488000, 380200, 488100).wkt, "wielkopolskie", "średzki", "Zaniemyśl", "")
    monkeypatch.setattr(cadastre, "fetch_parcel_by_xy", lambda lon, lat: p)
    app = FastAPI()
    app.include_router(routes_search.router)
    c = TestClient(app)
    r = c.get("/api/parcel-at", params={"lat": 52.2, "lon": 17.2}).json()
    assert r["parcel"]["parcel_id"] == "302505_5.0014.111/5" and r["parcel"]["area_ha"] == 2.0
    assert r["provenance"]["kind"] == "pomiar"
    monkeypatch.setattr(cadastre, "fetch_parcel_by_xy", lambda lon, lat: None)
    r = c.get("/api/parcel-at", params={"lat": 52.2, "lon": 17.2}).json()
    assert r["parcel"] is None and r["provenance"]["kind"] == "brak"
    assert c.get("/api/parcel-at", params={"lat": 40, "lon": 17.2}).status_code == 422
