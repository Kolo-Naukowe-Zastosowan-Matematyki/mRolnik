"""Kalkulator zysku z działek i optymalny plan upraw (czyste funkcje, bez FastAPI i bez sieci).

* Zysk działki = nadwyżka bezpośrednia bez dopłat:
  powierzchnia × (plon [dt/ha] × cena [zł/dt] − koszty bezpośrednie [zł/ha]).
  Koszty bezpośrednie (materiał siewny, nawozy, środki ochrony roślin, inne) – z kalkulacji ośrodka doradztwa
  rolniczego; plon i cenę podaje warstwa danych (GUS) albo rolnik. Dopłat nie liczymy – zależą od gospodarstwa.
* Optymalny plan: dla każdej działki uprawa o największej nadwyżce, przy warunkach:
  - na każdej działce inna uprawa niż obecnie (zmianowanie; norma GAEC 7 wymaga zmiany na co najmniej 40% gruntów
    ornych – plan zmienia uprawę wszędzie),
  - gdy grunty orne > 10 ha (GAEC 7, dywersyfikacja): co najmniej 3 różne uprawy, największa ≤ 65% gruntów ornych,
    dwie największe łącznie ≤ 90%.
  Odrębna uprawa (GAEC 7): rodzaj roślin; formy ozima i jara tego samego rodzaju to odrębne uprawy.
  Trwałe użytki zielone nie są gruntami ornymi – zostają bez zmian.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

GAEC7_MIN_ARABLE_HA = 10.0
GAEC7_MIN_CROPS = 3
GAEC7_MAX_MAIN = 0.65
GAEC7_MAX_TWO = 0.90


@dataclass(frozen=True)
class CropEcon:
    key: str                         # identyfikator uprawy (odrębna uprawa wg GAEC 7)
    name: str
    yield_dt_ha: Optional[float]
    price_zl_dt: Optional[float]
    direct_cost_zl_ha: Optional[float]
    group: str = ""                  # zboża / oleiste / okopowe / bobowate / kukurydza
    legume: bool = False

    @property
    def complete(self) -> bool:
        return all(v is not None and v >= 0 for v in (self.yield_dt_ha, self.price_zl_dt, self.direct_cost_zl_ha)) \
            and (self.yield_dt_ha or 0) > 0 and (self.price_zl_dt or 0) > 0

    def margin_ha(self) -> Optional[float]:
        if not self.complete:
            return None
        return self.yield_dt_ha * self.price_zl_dt - self.direct_cost_zl_ha


@dataclass
class ParcelIn:
    pid: str
    area_ha: float
    current: Optional[str] = None    # klucz obecnej uprawy (None = nieznana)
    arable: bool = True


def parcel_profit(area_ha: float, yield_dt_ha: float, price_zl_dt: float, direct_cost_zl_ha: float) -> Dict[str, float]:
    revenue = area_ha * yield_dt_ha * price_zl_dt
    cost = area_ha * direct_cost_zl_ha
    return {"revenue_zl": round(revenue, 0), "direct_cost_zl": round(cost, 0), "margin_zl": round(revenue - cost, 0),
            "margin_zl_ha": round(yield_dt_ha * price_zl_dt - direct_cost_zl_ha, 0)}


def shares(assign: Sequence[Tuple[float, str]]) -> List[Tuple[str, float]]:
    """[(uprawa, udział w gruntach ornych)] malejąco."""
    total = sum(a for a, _ in assign)
    by: Dict[str, float] = {}
    for a, k in assign:
        by[k] = by.get(k, 0.0) + a
    return sorted(((k, (v / total if total else 0.0)) for k, v in by.items()), key=lambda x: -x[1])


def gaec7_check(assign: Sequence[Tuple[float, str]], changed_share: Optional[float] = None) -> List[Dict[str, object]]:
    """Warunki normy GAEC 7 dla planu na gruntach ornych: [(powierzchnia, uprawa)]."""
    arable = sum(a for a, _ in assign)
    out: List[Dict[str, object]] = []
    if changed_share is not None:
        out.append({"rule": "zmiana", "ok": changed_share >= 0.4 - 1e-9,
                    "text": f"Inna uprawa niż w tym roku na {changed_share * 100:.0f}% gruntów ornych (wymóg: co najmniej 40%)."})
    if arable <= GAEC7_MIN_ARABLE_HA:
        out.append({"rule": "dywersyfikacja", "ok": True,
                    "text": f"Grunty orne {arable:.2f} ha – do 10 ha nie obowiązuje wymóg 3 upraw."})
        return out
    sh = shares(assign)
    n = len(sh)
    main = sh[0][1] if sh else 0.0
    two = sum(s for _, s in sh[:2])
    out.append({"rule": "liczba_upraw", "ok": n >= GAEC7_MIN_CROPS, "text": f"Różnych upraw: {n} (wymóg: co najmniej 3)."})
    out.append({"rule": "uprawa_glowna", "ok": main <= GAEC7_MAX_MAIN + 1e-9,
                "text": f"Największa uprawa: {main * 100:.0f}% gruntów ornych (wymóg: najwyżej 65%)."})
    out.append({"rule": "dwie_glowne", "ok": two <= GAEC7_MAX_TWO + 1e-9,
                "text": f"Dwie największe uprawy: {two * 100:.0f}% (wymóg: najwyżej 90%)."})
    return out


def _feasible(assign: Sequence[Tuple[float, str]]) -> bool:
    return all(c["ok"] for c in gaec7_check(assign))


def _violation(assign: Sequence[Tuple[float, str]]) -> float:
    """Miara złamania warunków dywersyfikacji (0 = spełnione)."""
    if sum(a for a, _ in assign) <= GAEC7_MIN_ARABLE_HA:
        return 0.0
    sh = shares(assign)
    v = max(0, GAEC7_MIN_CROPS - len(sh)) * 1.0
    v += max(0.0, sh[0][1] - GAEC7_MAX_MAIN) if sh else 0.0
    v += max(0.0, sum(s for _, s in sh[:2]) - GAEC7_MAX_TWO)
    return v


@dataclass
class PlanResult:
    rows: List[Dict[str, object]] = field(default_factory=list)
    total_margin_zl: float = 0.0
    current_margin_zl: Optional[float] = None
    checks: List[Dict[str, object]] = field(default_factory=list)
    feasible: bool = True
    notes: List[str] = field(default_factory=list)


def optimal_plan(parcels: Sequence[ParcelIn], catalog: Sequence[CropEcon]) -> PlanResult:
    """Plan upraw na następny sezon: maksymalna łączna nadwyżka bezpośrednia przy warunkach zmianowania i GAEC 7.

    Najpierw każda działka dostaje najlepszą dozwoloną uprawę, potem – jeśli trzeba – naprawa dywersyfikacji:
    kolejno zmiany pojedynczych działek, które najbardziej zmniejszają złamanie warunków przy najmniejszej stracie."""
    econ = {c.key: c for c in catalog if c.complete}
    res = PlanResult()
    if not econ:
        res.feasible = False
        res.notes.append("Brak upraw z pełnymi danymi (plon, cena, koszty) – nie da się policzyć planu.")
        return res
    arable = [p for p in parcels if p.arable and p.area_ha > 0]
    ranked: Dict[str, List[str]] = {}
    for p in arable:
        allowed = [k for k in econ if k != p.current]
        ranked[p.pid] = sorted(allowed, key=lambda k: -econ[k].margin_ha())
    choice = {p.pid: ranked[p.pid][0] for p in arable if ranked[p.pid]}
    area = {p.pid: p.area_ha for p in arable}

    def assign_of(ch: Dict[str, str]) -> List[Tuple[float, str]]:
        return [(area[pid], k) for pid, k in ch.items()]

    def value(ch: Dict[str, str]) -> float:
        return sum(area[pid] * econ[k].margin_ha() for pid, k in ch.items())

    # naprawa dywersyfikacji: zachłannie po ruchach pojedynczej działki (najpierw parami, gdy pojedyncze nie pomagają)
    for _ in range(4 * max(1, len(choice))):
        cur_v = _violation(assign_of(choice))
        if cur_v <= 1e-9:
            break
        best = None
        for pid, k_now in choice.items():
            for k in ranked[pid]:
                if k == k_now:
                    continue
                trial = dict(choice, **{pid: k})
                v = _violation(assign_of(trial))
                if v < cur_v - 1e-9:
                    loss = value(choice) - value(trial)
                    score = (cur_v - v) / (loss + 1.0)
                    if best is None or score > best[0]:
                        best = (score, trial)
        if best is None:
            # dwie zmiany naraz (np. gdy brakuje dwóch upraw)
            for (p1, k1), (p2, k2) in itertools.combinations(choice.items(), 2):
                for a, b in itertools.product(ranked[p1][:6], ranked[p2][:6]):
                    if a == k1 and b == k2:
                        continue
                    trial = dict(choice, **{p1: a, p2: b})
                    v = _violation(assign_of(trial))
                    if v < cur_v - 1e-9:
                        loss = value(choice) - value(trial)
                        score = (cur_v - v) / (loss + 1.0)
                        if best is None or score > best[0]:
                            best = (score, trial)
        if best is None:
            break
        choice = best[1]

    plan = assign_of(choice)
    changed = sum(area[pid] for pid, k in choice.items() if k != next(p.current for p in arable if p.pid == pid))
    total_arable = sum(area.values())
    res.checks = gaec7_check(plan, changed / total_arable if total_arable else None)
    res.feasible = all(c["ok"] for c in res.checks)
    if not res.feasible:
        big = max(arable, key=lambda p: p.area_ha)
        if total_arable > GAEC7_MIN_ARABLE_HA and big.area_ha / total_arable > GAEC7_MAX_MAIN:
            res.notes.append(f"Jedna działka zajmuje {big.area_ha / total_arable * 100:.0f}% gruntów ornych – warunku 65% nie da się "
                             "spełnić bez podzielenia jej na dwie uprawy.")
        else:
            res.notes.append("Przy tych działkach i uprawach nie udało się spełnić wszystkich warunków – sprawdź listę poniżej.")
    cur_total = 0.0
    cur_known = True
    for p in parcels:
        if not p.arable or p.pid not in choice:
            res.rows.append({"pid": p.pid, "area_ha": p.area_ha, "current": p.current, "plan": p.current, "kept": True,
                             "margin_zl": None, "margin_zl_ha": None})
            continue
        e = econ[choice[p.pid]]
        prof = parcel_profit(p.area_ha, e.yield_dt_ha, e.price_zl_dt, e.direct_cost_zl_ha)
        res.rows.append({"pid": p.pid, "area_ha": p.area_ha, "current": p.current, "plan": e.key, "kept": False, **prof})
        res.total_margin_zl += prof["margin_zl"]
        ce = econ.get(p.current or "")
        if ce is None:
            cur_known = False
        else:
            cur_total += p.area_ha * ce.margin_ha()
    res.total_margin_zl = round(res.total_margin_zl, 0)
    res.current_margin_zl = round(cur_total, 0) if cur_known and arable else None
    return res
