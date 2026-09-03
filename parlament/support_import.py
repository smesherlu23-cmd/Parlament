"""Чтение таблицы поддержки из CSV.

Формат — обычная таблица, какую удобно вести в Excel или Google Таблицах:
округ, населённый пункт, дальше по столбцу на партию.

    Округ,Населённый пункт,Народный союз,Партия труда,Аграрный блок
    Гаффинсвик центр,Верхний квартал,4,2,
    Гаффинсвик центр,Гавань,,3,3

Разделитель определяется сам (запятая или точка с запятой — русский Excel
сохраняет со второй), пустая клетка считается нулём. Округа и партии
сопоставляются по названию без учёта регистра и лишних пробелов, потому что
руками набранное «  гаффинсвик центр » должно находиться так же, как точное.

Названия, которых нет в проекте, не создаются молча, а возвращаются списком
замечаний: опечатка в документе иначе тихо съела бы часть таблицы.
Населённые пункты — наоборот, заводятся: их в игровой карте нет, и таблица
для того и нужна, чтобы не набивать список руками.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field


@dataclass
class SupportImportResult:
    """Разобранная таблица поддержки."""

    #: `{district_id: {название НП: {party_id: очки}}}` — названия, а не id,
    #: потому что пункты в проекте могут ещё не существовать и будут созданы.
    rows: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def settlements_filled(self) -> int:
        return sum(len(v) for v in self.rows.values())


def parse_support_csv(text: str, districts: dict[str, str],
                      parties: dict[str, str]) -> SupportImportResult:
    """Разбирает таблицу «округ, населённый пункт, очки по партиям».

        Округ,Населённый пункт,Народный союз,Партия труда
        Гаффинсвик центр,Гаффинсвик-Сити,4,2

    Правила сопоставления те же, что у таблицы результатов: разделитель
    определяется сам, названия сверяются без учёта регистра и лишних пробелов,
    незнакомые округа и партии не создаются молча, а попадают в замечания.
    """
    result = SupportImportResult()

    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")),
                           delimiter=_sniff_delimiter(text)))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        result.warnings.append(
            "В файле нет данных: нужна строка заголовка (округ, населённый пункт, "
            "названия партий) и хотя бы одна строка пункта."
        )
        return result

    by_district = {_key(name): did for name, did in districts.items()}
    by_party = {_key(name): pid for name, pid in parties.items()}

    header = rows[0]
    columns: list[tuple[int, str | None, str]] = []
    for index, title in enumerate(header[2:], start=2):
        name = title.strip()
        if not name:
            continue
        party_id = by_party.get(_key(name))
        if party_id is None:
            result.warnings.append(
                f"Партия «{name}» не найдена в справочнике — столбец пропущен.")
        columns.append((index, party_id, name))

    if not any(party_id for _i, party_id, _n in columns):
        result.warnings.append("Ни один столбец не совпал с партией из справочника.")
        return result

    for row in rows[1:]:
        district_name = (row[0].strip() if row else "")
        settlement_name = (row[1].strip() if len(row) > 1 else "")
        if not district_name and not settlement_name:
            continue
        district_id = by_district.get(_key(district_name))
        if district_id is None:
            result.warnings.append(
                f"Округ «{district_name}» не найден на карте — строка пропущена.")
            continue
        if not settlement_name:
            # Пустая строка под округом — заготовка из шаблона: он выдаёт такую
            # там, где пунктов ещё нет. Это приглашение заполнить, а не ошибка.
            # Ругаемся только если очки проставили, а пункт назвать забыли.
            if any(index < len(row) and row[index].strip()
                   for index, _party, _name in columns):
                result.warnings.append(
                    f"«{district_name}»: очки есть, а названия населённого пункта "
                    f"нет — строка пропущена.")
            continue

        points: dict[str, int] = {}
        for index, party_id, party_name in columns:
            if party_id is None or index >= len(row):
                continue
            cell = row[index].strip()
            if not cell:
                continue
            value = _parse_number(cell)
            if value is None:
                result.warnings.append(
                    f"«{settlement_name}», {party_name}: «{cell}» — не число, "
                    f"клетка пропущена.")
                continue
            if value > 0:
                points[party_id] = value

        result.rows.setdefault(district_id, {})[settlement_name] = points

    if not result.rows:
        result.warnings.append("Ни одного населённого пункта разобрать не удалось.")
    return result


def _sniff_delimiter(text: str) -> str:
    """Запятая или точка с запятой — что чаще встречается в первой строке."""
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return ";" if first.count(";") > first.count(",") else ","


def _key(name: str) -> str:
    """Ключ сопоставления: без регистра, без краевых и двойных пробелов."""
    return " ".join(name.split()).casefold()


def _parse_number(cell: str) -> int | None:
    """Целое число из клетки. Терпит пробелы-разделители тысяч («12 500»),
    неразрывные пробелы и дробное «1500,0» — но не выдумывает числа из букв."""
    text = cell.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number != int(number):
        return None
    return int(number)
