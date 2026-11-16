"""Koordynacja w zlewni rowu (spółka wodna, sąsiedzi) – WYŁĄCZNIE DEMONSTRACJA.

Brak publicznego rejestru, z którego można by pobrać sąsiadów i ich zgody; dane osobowe sąsiadów nie są
przetwarzane. Gospodarstwa są zanonimizowane ("Gospodarstwo A/B/C"), a moduł nie wysyła żadnych powiadomień.
"""
from __future__ import annotations

from typing import Any, Dict

from src.data.common import provenance
from src.hydromelior.network import is_demo_area


def get_catchment_coordination_status(lat: float, lon: float) -> Dict[str, Any]:
    if not is_demo_area(lat, lon):
        return {
            "demo": False, "available": False, "neighbors": [], "action_log": [],
            "note": ("Koordynacja z sąsiadami wymaga danych spółki wodnej i zgody uczestników; nie jest dostępna dla tej lokalizacji. "
                     "Przykład działania można zobaczyć dla gospodarstwa demo w Śnieciskach."),
            "provenance": provenance("mRolnik", "", "brak", note="Brak źródła danych o spółkach wodnych i sąsiadach."),
        }
    return {
        "demo": True, "available": True,
        "note": "PRZYKŁAD: dane fikcyjne i zanonimizowane, pokazujące jak mogłaby wyglądać koordynacja w spółce wodnej. Aplikacja nie wysyła powiadomień.",
        "cooperative": {"name": "Spółka wodna (przykład)", "catchment": "Zlewnia rowów R-1 i R-2 (przykład)"},
        "neighbors": [
            {"farm": "Gospodarstwo A", "position": "poniżej zastawki Z-1", "distance_m": 180, "land_use": "łąka (TUZ)",
             "expected_impact": "korzystny – podsiąk przy piętrzeniu", "agreement": "przykład: uzgodnione ustnie"},
            {"farm": "Gospodarstwo B", "position": "powyżej zastawki Z-1", "distance_m": 320, "land_use": "grunt orny",
             "expected_impact": "neutralny", "agreement": "przykład: brak zastrzeżeń"},
            {"farm": "Gospodarstwo C", "position": "przy wylocie rowu R-2", "distance_m": 450, "land_use": "grunt orny",
             "expected_impact": "do obserwacji przy dużym opadzie", "agreement": "przykład: wymaga uzgodnienia"},
        ],
        "action_log": [
            {"date": "przykład", "actor": "Gospodarstwo (użytkownik)", "action": "Planowane przymknięcie zastawki Z-1"},
            {"date": "przykład", "actor": "Spółka wodna", "action": "Konserwacja rowu R-1"},
        ],
        "provenance": provenance("Dane przykładowe mRolnik", "", "demo", note="Dane fikcyjne do demonstracji funkcji."),
    }
