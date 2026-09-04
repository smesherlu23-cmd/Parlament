"""Мост между файлом на диске и разбором таблицы поддержки.

Сам разбор живёт в `parlament.support_import` и про Flet ничего не знает.
Здесь — только то, что связано с файлами: раскодировать байты, отдать
шаблон под текущие округа, пункты и партии.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .. import district_seed
from ..elections import SETTLEMENT_SUPPORT
from ..model import Project
from ..support_import import SupportImportResult, parse_support_csv

#: Кодировки, которыми обычно оказывается сохранён CSV с русским текстом.
#: UTF-8 первым, следом то, во что сохраняет русский Excel.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")


def read_support_file(picked, project: Project) -> SupportImportResult:
    """Разбирает выбранный файл с таблицей поддержки."""
    return parse_support_text(_read_bytes(picked), project)


def parse_support_text(data: bytes, project: Project) -> SupportImportResult:
    def capacity(district_id: str, name: str) -> int:
        """Запас очков сельского пункта — по его собственной записи."""
        district = project.district(district_id)
        key = " ".join(name.split()).casefold()
        for settlement in district.settlements if district else ():
            if " ".join(settlement.name.split()).casefold() == key:
                return settlement.capacity
        return SETTLEMENT_SUPPORT

    def city_capacity(city_id: str) -> int:
        city = project.city_by_id(city_id)
        return city.capacity if city else SETTLEMENT_SUPPORT

    # Городские округа («Гаффинсвик центр» и т. п.) в список округов для
    # сопоставления не идут: своих очков у них нет, всё через `cities` —
    # общую копилку метрополии.
    return parse_support_csv(
        _decode(data),
        {d.name: d.id for d in project.districts if not district_seed.is_city(d.code)},
        {p.name: p.id for p in project.parties},
        cities={c.name: c.id for c in project.cities},
        capacity=capacity,
        city_capacity=city_capacity,
    )


def export_support_template(project: Project) -> bytes:
    """Заготовка таблицы поддержки под текущие округа, пункты, города и партии.

    Пункты выгружаются со своими очками; у округа без пунктов остаётся
    пустая строка — чтобы было видно, что заполнить. Городские округа
    («Гаффинсвик центр» и т. п.) по отдельности не выгружаются: их очки
    общие на весь город, и город выгружается одной строкой под своим именем,
    а не под именем каждого своего округа. Сохраняем с BOM, иначе Excel
    открывает кириллицу кракозябрами.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Округ", "Населённый пункт", *[p.name for p in project.parties]])
    for district in project.districts:
        if district_seed.is_city(district.code):
            continue
        if district.settlements:
            for settlement in district.settlements:
                writer.writerow([
                    district.name, settlement.name,
                    *[settlement.support.get(p.id, "") or "" for p in project.parties],
                ])
        else:
            writer.writerow([district.name, "", *[""] * len(project.parties)])
    for city in project.cities:
        writer.writerow([
            city.name, city.name,
            *[city.support.get(p.id, "") or "" for p in project.parties],
        ])
    return buffer.getvalue().encode("utf-8-sig")


def _read_bytes(picked) -> bytes:
    data = getattr(picked, "bytes", None)
    if data:
        return bytes(data)
    path = getattr(picked, "path", None)
    if not path:
        raise ValueError("файл не передан")
    return Path(path).read_bytes()


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Последняя попытка: не падать из-за одного нечитаемого байта, разбор
    # всё равно сообщит, если содержимое окажется бессмысленным.
    return data.decode("utf-8", errors="replace")
