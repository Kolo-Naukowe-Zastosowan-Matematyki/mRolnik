"""Tabela stacji synoptycznych IMGW-PIB (kod, nazwa, przybliżone współrzędne, kod TERYT województwa).

API https://danepubliczne.imgw.pl/api/data/synop zwraca `id_stacji` i `stacja`, ale NIE zwraca współrzędnych,
dlatego najbliższą stację wybieramy z tej tabeli. Współrzędne są przybliżone (dokładność rzędu kilku km,
wystarczająca do wyboru najbliższej stacji). Rekordy z API dopasowujemy po kodzie stacji, a gdy kod
się nie zgadza – po znormalizowanej nazwie, więc pojedyncza pomyłka w kodzie nie psuje dopasowania.
Stacje wysokogórskie (mountain=True) pomijamy przy wyborze stacji dla pola uprawnego.
"""
from __future__ import annotations

from typing import List, NamedTuple


class SynopStation(NamedTuple):
    id: str
    name: str
    lat: float
    lon: float
    voiv_teryt: str
    mountain: bool = False


SYNOP_STATIONS: List[SynopStation] = [
    SynopStation("12100", "Kołobrzeg", 54.18, 15.58, "32"),
    SynopStation("12105", "Koszalin", 54.20, 16.15, "32"),
    SynopStation("12115", "Ustka", 54.59, 16.85, "22"),
    SynopStation("12120", "Łeba", 54.75, 17.53, "22"),
    SynopStation("12125", "Lębork", 54.55, 17.75, "22"),
    SynopStation("12135", "Hel", 54.60, 18.81, "22"),
    SynopStation("12155", "Gdańsk", 54.38, 18.47, "22"),
    SynopStation("12160", "Elbląg", 54.17, 19.43, "28"),
    SynopStation("12185", "Kętrzyn", 54.07, 21.37, "28"),
    SynopStation("12195", "Suwałki", 54.13, 22.95, "20"),
    SynopStation("12200", "Świnoujście", 53.92, 14.24, "32"),
    SynopStation("12205", "Szczecin", 53.40, 14.62, "32"),
    SynopStation("12210", "Resko", 53.77, 15.42, "32"),
    SynopStation("12215", "Szczecinek", 53.72, 16.68, "32"),
    SynopStation("12230", "Piła", 53.13, 16.75, "30"),
    SynopStation("12235", "Chojnice", 53.70, 17.53, "22"),
    SynopStation("12250", "Toruń", 53.04, 18.60, "04"),
    SynopStation("12270", "Mława", 53.10, 20.36, "14"),
    SynopStation("12272", "Olsztyn", 53.77, 20.42, "28"),
    SynopStation("12280", "Mikołajki", 53.79, 21.58, "28"),
    SynopStation("12285", "Ostrołęka", 53.07, 21.56, "14"),
    SynopStation("12295", "Białystok", 53.11, 23.16, "20"),
    SynopStation("12300", "Gorzów", 52.74, 15.28, "08"),
    SynopStation("12310", "Słubice", 52.35, 14.60, "08"),
    SynopStation("12330", "Poznań", 52.42, 16.83, "30"),
    SynopStation("12345", "Koło", 52.20, 18.66, "30"),
    SynopStation("12360", "Płock", 52.59, 19.73, "14"),
    SynopStation("12375", "Warszawa", 52.16, 20.96, "14"),
    SynopStation("12385", "Siedlce", 52.18, 22.25, "14"),
    SynopStation("12399", "Terespol", 52.07, 23.62, "06"),
    SynopStation("12400", "Zielona Góra", 51.93, 15.53, "08"),
    SynopStation("12415", "Legnica", 51.19, 16.21, "02"),
    SynopStation("12418", "Leszno", 51.84, 16.53, "30"),
    SynopStation("12424", "Wrocław", 51.10, 16.88, "02"),
    SynopStation("12435", "Kalisz", 51.78, 18.08, "30"),
    SynopStation("12455", "Wieluń", 51.21, 18.56, "10"),
    SynopStation("12465", "Łódź", 51.72, 19.40, "10"),
    SynopStation("12469", "Sulejów", 51.35, 19.87, "10"),
    SynopStation("12488", "Kozienice", 51.56, 21.55, "14"),
    SynopStation("12495", "Lublin", 51.22, 22.39, "06"),
    SynopStation("12497", "Włodawa", 51.55, 23.53, "06"),
    SynopStation("12500", "Jelenia Góra", 50.90, 15.79, "02"),
    SynopStation("12510", "Śnieżka", 50.74, 15.74, "02", True),
    SynopStation("12520", "Kłodzko", 50.44, 16.61, "02"),
    SynopStation("12530", "Opole", 50.63, 17.97, "16"),
    SynopStation("12540", "Racibórz", 50.06, 18.19, "24"),
    SynopStation("12550", "Częstochowa", 50.81, 19.09, "24"),
    SynopStation("12560", "Katowice", 50.24, 19.03, "24"),
    SynopStation("12566", "Kraków", 50.08, 19.80, "12"),
    SynopStation("12570", "Kielce", 50.81, 20.69, "26"),
    SynopStation("12575", "Tarnów", 50.03, 20.98, "12"),
    SynopStation("12580", "Rzeszów", 50.11, 22.04, "18"),
    SynopStation("12585", "Sandomierz", 50.70, 21.72, "26"),
    SynopStation("12595", "Zamość", 50.70, 23.20, "06"),
    SynopStation("12600", "Bielsko Biała", 49.81, 19.00, "24"),
    SynopStation("12625", "Zakopane", 49.29, 19.96, "12"),
    SynopStation("12650", "Kasprowy Wierch", 49.23, 19.98, "12", True),
    SynopStation("12660", "Nowy Sącz", 49.63, 20.69, "12"),
    SynopStation("12670", "Krosno", 49.71, 21.77, "18"),
    SynopStation("12690", "Lesko", 49.47, 22.34, "18"),
    SynopStation("12695", "Przemyśl", 49.81, 22.77, "18"),
]

# Kody TERYT województw (2 cyfry) i ich nazwy w odpowiedziach ULDK
VOIVODESHIP_TERYT = {
    "dolnośląskie": "02", "kujawsko-pomorskie": "04", "lubelskie": "06", "lubuskie": "08",
    "łódzkie": "10", "małopolskie": "12", "mazowieckie": "14", "opolskie": "16",
    "podkarpackie": "18", "podlaskie": "20", "pomorskie": "22", "śląskie": "24",
    "świętokrzyskie": "26", "warmińsko-mazurskie": "28", "wielkopolskie": "30", "zachodniopomorskie": "32",
}


class HydroStation(NamedTuple):
    name: str
    river: str
    lat: float
    lon: float


# Wybrane wodowskazy IMGW-PIB (przybliżone współrzędne, dokładność ~1–3 km). Używane tylko wtedy, gdy
# odpowiedź API hydro nie zawiera współrzędnych; dopasowanie po znormalizowanej nazwie posterunku.
HYDRO_STATIONS: List[HydroStation] = [
    HydroStation("Poznań-Most Rocha", "Warta", 52.405, 16.947),
    HydroStation("Śrem", "Warta", 52.090, 17.020),
    HydroStation("Nowe Miasto nad Wartą", "Warta", 52.090, 17.410),
    HydroStation("Pyzdry", "Warta", 52.170, 17.690),
    HydroStation("Oborniki", "Warta", 52.650, 16.810),
    HydroStation("Skwierzyna", "Warta", 52.600, 15.500),
    HydroStation("Gorzów Wielkopolski", "Warta", 52.730, 15.240),
    HydroStation("Sieradz", "Warta", 51.600, 18.730),
    HydroStation("Kraków-Bielany", "Wisła", 50.040, 19.850),
    HydroStation("Sandomierz", "Wisła", 50.680, 21.750),
    HydroStation("Puławy", "Wisła", 51.410, 21.960),
    HydroStation("Warszawa-Bulwary", "Wisła", 52.250, 21.030),
    HydroStation("Płock", "Wisła", 52.540, 19.690),
    HydroStation("Toruń", "Wisła", 53.010, 18.600),
    HydroStation("Tczew", "Wisła", 54.090, 18.800),
    HydroStation("Racibórz-Miedonia", "Odra", 50.110, 18.200),
    HydroStation("Opole-Groszowice", "Odra", 50.630, 17.970),
    HydroStation("Wrocław-Trestno", "Odra", 51.080, 17.130),
    HydroStation("Głogów", "Odra", 51.660, 16.090),
    HydroStation("Słubice", "Odra", 52.350, 14.560),
    HydroStation("Gozdowice", "Odra", 52.760, 14.320),
    HydroStation("Krasnystaw", "Wieprz", 50.980, 23.170),
    HydroStation("Wyszków", "Bug", 52.590, 21.460),
    HydroStation("Ostrołęka", "Narew", 53.080, 21.570),
    HydroStation("Przemyśl", "San", 49.780, 22.770),
    HydroStation("Jarosław", "San", 50.020, 22.680),
    HydroStation("Nowy Sącz", "Dunajec", 49.620, 20.700),
    HydroStation("Bydgoszcz", "Brda", 53.120, 18.000),
]
