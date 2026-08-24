"""Форматирование строк интерфейса: склонения, проценты."""

from __future__ import annotations

SEATS = ["место", "места", "мест"]
PARTIES = ["партия", "партии", "партий"]
CONVOCATIONS = ["созыве", "созывах", "созывах"]
PARTIES_COUNT = ["партия", "партии", "партий"]
DISTRICTS = ["округ", "округа", "округов"]


def plural(count: int, forms: list[str]) -> str:
    """Форма существительного при числе: 1 место, 2 места, 5 мест."""
    n = abs(count) % 100
    if 11 <= n <= 19:
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def pluralize(count: int, forms: list[str]) -> str:
    """«5 мест» — число вместе со склонённым словом."""
    return f"{count} {plural(count, forms)}"


def percent(value: int, total: int) -> str:
    """«28,3 %» — с запятой, как принято в русской типографике."""
    if not total:
        return "0,0 %"
    return f"{value / total * 100:.1f}".replace(".", ",") + " %"
