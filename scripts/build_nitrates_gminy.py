"""Buduje src/data/nitrates_gminy.json – wykazy gmin z „Programu azotanowego” (terminy nawożenia azotem).

Źródło: Rozporządzenie Rady Ministrów z dnia 31 stycznia 2023 r. w sprawie „Programu działań mających na celu
zmniejszenie zanieczyszczenia wód azotanami pochodzącymi ze źródeł rolniczych oraz zapobieganie dalszemu
zanieczyszczeniu” (Dz.U. 2023 poz. 244), tekst ogłoszony: https://api.sejm.gov.pl/eli/acts/DU/2023/244/text.pdf
- Załącznik nr 2, tabela 4: gminy, w których nawozy azotowe mineralne i naturalne płynne stosuje się na gruntach ornych do 15 października,
- Załącznik nr 3, tabela 5: gminy z terminem do 25 października,
- pozostałe gminy: do 20 października (tabela 2).
Kody gmin to 7-cyfrowe kody TERYT (woj., powiat, gmina, rodzaj) – ten sam format co początek identyfikatora działki ULDK
(np. 302505_5 → 3025055).

Uruchomienie (jednorazowo; wymaga pakietu pypdf, którego aplikacja nie potrzebuje w trakcie pracy):
    .venv\\Scripts\\python -m pip install pypdf
    .venv\\Scripts\\python scripts\\build_nitrates_gminy.py
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = "https://api.sejm.gov.pl/eli/acts/DU/2023/244/text.pdf"
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "nitrates_gminy.json"


def extract(text: str) -> dict:
    t = re.sub(r"\s+", " ", text)
    i4 = t.find("Tabela 4. Wykaz gmin")
    i5 = t.find("Tabela 5. Wykaz gmin")
    if i4 < 0 or i5 < 0:
        raise SystemExit("Nie znaleziono tabel 4 i 5 w tekście rozporządzenia")
    code = re.compile(r"(?<!\d)(\d{7})(?!\d)")
    t4 = sorted(set(code.findall(t[i4:i5])))
    end5 = t.find("Załącznik nr 4", i5)     # tabela 5 kończy się przed załącznikiem nr 4 (obrót stada)
    t5 = sorted(set(code.findall(t[i5:end5 if end5 > 0 else None])))
    return {"oct15": t4, "oct25": t5}


def main() -> None:
    import pypdf  # noqa: WPS433 – tylko dla skryptu budującego

    pdf = requests.get(URL, headers={"User-Agent": "mRolnik/0.3 (skrypt programu azotanowego)"}, timeout=120).content
    text = " ".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    lists = extract(text)
    data = {
        "act": "Rozporządzenie Rady Ministrów z dnia 31 stycznia 2023 r. (Dz.U. 2023 poz. 244) – „Program azotanowy”",
        "source_url": "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20230000244",
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "note": "Kody TERYT gmin z załącznika nr 2 (tabela 4, termin 15 października) i nr 3 (tabela 5, termin 25 października).",
        **lists,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Zapisano {OUT}: {len(lists['oct15'])} gmin (15 X), {len(lists['oct25'])} gmin (25 X)")


if __name__ == "__main__":
    main()
