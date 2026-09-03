"""Чтение результатов выборов из таблицы (CSV).

Формат — обычная таблица, какую удобно вести в Excel или Google Таблицах:
первый столбец с названием округа, дальше по столбцу на партию.

    Округ,Народный союз,Партия труда,Аграрный блок
    Гаффинсвик центр,5000,3000,2000
    Саттмалвик центр,,9000,

Разделитель определяется сам (запятая или точка с запятой — русский Excel
сохраняет со второй), пустая клетка считается нулём. Округа и партии
сопоставляются по названию без учёта регистра и лишних пробелов, потому что
руками набранное «  гаффинсвик центр » должно находиться так же, как точное.

Названия, которых нет в проекте, не создаются молча, а возвращаются списком
замечаний: опечатка в документе иначе тихо съела бы часть результатов.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field


@dataclass
class ImportResult:
    """Разобранная таблица: что удалось сопоставить и что вызвало вопросы."""

    #: `{district_id: {party_id: голоса}}` — прямо в таком виде это принимает
    #: `ParlamentService.run_election`.
    votes: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Замечания для показа пользователю: незнакомые округа и партии, мусор
    #: в клетках. Импорт при этом не отменяется — приходит то, что разобралось.
    warnings: list[str] = field(default_factory=list)

    @property
    def districts_filled(self) -> int:
        return len(self.votes)


def parse_votes_csv(text: str, districts: dict[str, str],
                    parties: dict[str, str]) -> ImportResult:
    """Разбирает таблицу результатов.

    :param text: содержимое файла.
    :param districts: `{название округа: id}` из проекта.
    :param parties: `{название партии: id}` из проекта.
    """
    result = ImportResult()

    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")),
                           delimiter=_sniff_delimiter(text)))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        result.warnings.append(
            "В файле нет данных: нужна строка заголовка с названиями партий "
            "и хотя бы одна строка округа."
        )
        return result

    by_district = {_key(name): did for name, did in districts.items()}
    by_party = {_key(name): pid for name, pid in parties.items()}

    header = rows[0]
    # Первый столбец — округ, дальше партии. Столбец без названия пропускаем:
    # в выгрузках из таблиц часто болтается пустой хвост.
    columns: list[tuple[int, str | None, str]] = []
    for index, title in enumerate(header[1:], start=1):
        name = title.strip()
        if not name:
            continue
        party_id = by_party.get(_key(name))
        if party_id is None:
            result.warnings.append(f"Партия «{name}» не найдена в справочнике — столбец пропущен.")
        columns.append((index, party_id, name))

    if not any(party_id for _i, party_id, _n in columns):
        result.warnings.append("Ни один столбец не совпал с партией из справочника.")
        return result

    for row in rows[1:]:
        district_name = (row[0].strip() if row else "")
        if not district_name:
            continue
        district_id = by_district.get(_key(district_name))
        if district_id is None:
            result.warnings.append(f"Округ «{district_name}» не найден на карте — строка пропущена.")
            continue

        votes: dict[str, int] = {}
        for index, party_id, party_name in columns:
            if party_id is None or index >= len(row):
                continue
            cell = row[index].strip()
            if not cell:
                continue
            value = _parse_number(cell)
            if value is None:
                result.warnings.append(
                    f"«{district_name}», {party_name}: «{cell}» — не число, клетка пропущена."
                )
                continue
            if value > 0:
                votes[party_id] = value

        if votes:
            result.votes[district_id] = votes

    if not result.votes:
        result.warnings.append("Ни одной строки с результатами разобрать не удалось.")
    return result


def _sniff_delimiter(text: str) -> str:
    """Запятая или точка с запятой — что чаще встречается в первой строке."""
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return ";" if first.count(";") > first.count(",") else ","


def _key(name: str) -> str:
    """Ключ сопоставления: без регистра, без краевых и двойных пробелов."""
    return " ".join(name.split()).casefold()


def _parse_number(cell: str) -> int | None:
    """Число голосов из клетки. Терпит пробелы-разделители тысяч («12 500»),
    неразрывные пробелы и дробное «1500,0» — но не выдумывает числа из букв."""
    text = cell.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number != int(number):
        return None
    return int(number)


# -- таблица поддержки -------------------------------------------------------
#
# Голоса больше не вводятся руками, зато руками заводятся населённые пункты и
# очки популярности — а их много: пункты в каждом из 27 округов, и в каждом
# по столбцу на партию. Это ровно та работа, которую удобнее делать в Excel.

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
