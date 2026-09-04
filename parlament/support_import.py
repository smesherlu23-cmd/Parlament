"""Чтение таблицы поддержки из CSV.

Формат — обычная таблица, какую удобно вести в Excel или Google Таблицах:
округ, населённый пункт, дальше по столбцу на партию.

    Округ,Населённый пункт,Народный союз,Партия труда,Аграрный блок
    Западный берег,Сандавик,4,2,
    Гаффинсвик,Гаффинсвик,,3,3

Разделитель определяется сам (запятая или точка с запятой — русский Excel
сохраняет со второй), пустая клетка считается нулём. Округа и партии
сопоставляются по названию без учёта регистра и лишних пробелов, потому что
руками набранное «  гаффинсвик центр » должно находиться так же, как точное.

Названия, которых нет в проекте, не создаются молча, а возвращаются списком
замечаний: опечатка в документе иначе тихо съела бы часть таблицы.
Населённые пункты — наоборот, заводятся: их в игровой карте нет, и таблица
для того и нужна, чтобы не набивать список руками.

Город — особая строка: округ этой строки не сельский, а название города
(«Гаффинсвик», а не «Гаффинсвик центр» — конкретный округ города своих очков
не хранит, они общие на всех его округах). Такая строка узнаётся по
совпадению с известным городом и не создаёт населённого пункта — очки идут
прямо в общую копилку.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Callable

from .elections import CITY_SUPPORT, SETTLEMENT_SUPPORT


@dataclass
class SupportImportResult:
    """Разобранная таблица поддержки."""

    #: `{district_id: {название НП: {party_id: очки}}}` — названия, а не id,
    #: потому что пункты в проекте могут ещё не существовать и будут созданы.
    rows: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    #: `{city_id: {party_id: очки}}` — города не создаются таблицей, они уже
    #: есть в проекте, поэтому сразу по id.
    city_rows: dict[str, dict[str, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def settlements_filled(self) -> int:
        return sum(len(v) for v in self.rows.values()) + len(self.city_rows)


def parse_support_csv(text: str, districts: dict[str, str],
                      parties: dict[str, str],
                      cities: dict[str, str] | None = None,
                      capacity: Callable[[str, str], int] | None = None,
                      city_capacity: Callable[[str], int] | None = None,
                      ) -> SupportImportResult:
    """Разбирает таблицу «округ, населённый пункт, очки по партиям».

        Округ,Населённый пункт,Народный союз,Партия труда
        Западный берег,Сандавик,4,2

    Разделитель определяется сам, названия сверяются без учёта регистра и
    лишних пробелов, незнакомые округа и партии не создаются молча, а
    попадают в замечания.

    :param districts: `{название сельского округа: id}` — городские округа
                      сюда не входят, их очки идут через `cities`.
    :param cities: `{название города: id}` — например, «Гаффинсвик», а не
                   «Гаффинсвик центр»: город один на несколько округов.
    :param capacity: `(district_id, название пункта) -> запас очков` для
                     сельских пунктов — а перебор надо поймать до записи.
    :param city_capacity: `city_id -> запас очков` для городов.
    """
    limit = capacity or (lambda _district_id, _name: SETTLEMENT_SUPPORT)
    city_limit = city_capacity or (lambda _city_id: CITY_SUPPORT)
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
    by_city = {_key(name): cid for name, cid in (cities or {}).items()}
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
        city_id = by_city.get(_key(district_name)) if district_id is None else None
        if district_id is None and city_id is None:
            result.warnings.append(
                f"Округ «{district_name}» не найден на карте — строка пропущена.")
            continue

        if city_id is not None:
            points, warnings = _read_points(row, columns, settlement_name)
            result.warnings.extend(warnings)
            total = sum(points.values())
            allowed = city_limit(city_id)
            if total > allowed:
                result.warnings.append(
                    f"«{district_name}»: роздано {total} очков, а в городе их "
                    f"{allowed} — строка пропущена.")
                continue
            if city_id in result.city_rows:
                result.warnings.append(
                    f"Город «{district_name}» встречается в таблице дважды — "
                    f"взята последняя строка.")
            result.city_rows[city_id] = points
            continue

        if not settlement_name:
            # Пустая строка под округом — заготовка из шаблона: он выдаёт такую
            # там, где пунктов ещё нет. Это приглашение заполнить, а не ошибка.
            # Ругаемся только если очки проставили, а пункт назвать забыли —
            # и только по столбцам знакомых партий: про чужие уже сказано выше.
            if any(index < len(row) and row[index].strip()
                   for index, party_id, _name in columns if party_id):
                result.warnings.append(
                    f"«{district_name}»: очки есть, а названия населённого пункта "
                    f"нет — строка пропущена.")
            continue

        points, warnings = _read_points(row, columns, settlement_name)
        result.warnings.extend(warnings)
        total = sum(points.values())
        allowed = limit(district_id, settlement_name)
        if total > allowed:
            # Ловим здесь, а не при записи в проект: так пользователь получает
            # список всех перебравших строк разом и правит документ за один
            # заход, а остальная таблица всё же загружается.
            result.warnings.append(
                f"«{settlement_name}»: роздано {total} очков, а в населённом "
                f"пункте их {allowed} — строка пропущена.")
            continue

        already = result.rows.setdefault(district_id, {})
        if settlement_name in already:
            result.warnings.append(
                f"«{settlement_name}» в округе «{district_name}» встречается "
                f"дважды — взята последняя строка.")
        already[settlement_name] = points

    if not result.rows and not result.city_rows:
        result.warnings.append("Ни одного населённого пункта разобрать не удалось.")
    return result


def _read_points(row: list[str], columns: list[tuple[int, str | None, str]],
                 place_name: str) -> tuple[dict[str, int], list[str]]:
    """Очки по столбцам партий одной строки — без проверки общей суммы."""
    points: dict[str, int] = {}
    warnings: list[str] = []
    for index, party_id, party_name in columns:
        if party_id is None or index >= len(row):
            continue
        cell = row[index].strip()
        if not cell:
            continue
        value = _parse_number(cell)
        if value is None:
            # Не «не число»: «1,5» и «-2» — числа, просто очки бывают только
            # целыми и неотрицательными.
            warnings.append(
                f"«{place_name}», {party_name}: «{cell}» не подходит — нужно "
                f"целое число очков от нуля. Клетка пропущена.")
            continue
        if value > 0:
            points[party_id] = value
    return points, warnings


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
    text = cell.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number != int(number):
        return None
    return int(number)
