"""Moduł bazy upraw w Polsce i ich grup retencyjnych względem bilansu i retencji wody.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CROPS_FILE = Path(__file__).with_name("crops_retention.json")

_DATA_CACHE: Optional[Dict[str, Any]] = None


def _load_data() -> Dict[str, Any]:
    global _DATA_CACHE
    if _DATA_CACHE is None:
        if CROPS_FILE.exists():
            with open(CROPS_FILE, "r", encoding="utf-8") as f:
                _DATA_CACHE = json.load(f)
        else:
            _DATA_CACHE = {"retention_groups": {}, "crops": []}
    return _DATA_CACHE


def get_retention_groups() -> Dict[str, Dict[str, Any]]:
    """Zwraca słownik wszystkich grup retencyjnych."""
    return _load_data().get("retention_groups", {})


def get_retention_group(group_id: str) -> Optional[Dict[str, Any]]:
    """Zwraca dane konkretnej grupy retencyjnej."""
    return get_retention_groups().get(group_id)


def list_crops() -> List[Dict[str, Any]]:
    """Zwraca pełną listę zdefiniowanych upraw w Polsce."""
    return _load_data().get("crops", [])


def find_crop(query: str) -> Optional[Dict[str, Any]]:
    """Wyszukuje uprawę po identyfikatorze lub nazwie (dokładnej lub częściowej)."""
    if not query:
        return None
    q = query.strip().lower()
    crops = list_crops()

    # 1. Dokładne dopasowanie ID lub nazwy
    for c in crops:
        if c.get("id", "").lower() == q or c.get("name", "").lower() == q:
            return c

    # 2. Zawieranie w nazwie
    for c in crops:
        if q in c.get("name", "").lower():
            return c

    return None


def get_crop_retention_group(crop_name_or_id: str) -> Optional[Dict[str, Any]]:
    """Zwraca grupę retencyjną powiązaną z uprawą lub kluczem grupy."""
    if not crop_name_or_id:
        return None
    groups = get_retention_groups()
    if crop_name_or_id in groups:
        return groups[crop_name_or_id]
    crop = find_crop(crop_name_or_id)
    if not crop:
        return None
    group_id = crop.get("retention_group_id") or crop.get("group_id")
    if not group_id:
        return None
    return groups.get(group_id)
