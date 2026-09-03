"""Мост между файлом на диске и разбором таблицы результатов.

Сам разбор живёт в `parlament.votes_import` и про Flet ничего не знает.
Здесь — только то, что связано с файлами: раскодировать байты, отдать
шаблон под текущие округа и партии.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..model import Project
from ..votes_import import (
    ImportResult,
    SupportImportResult,
    parse_support_csv,
    parse_votes_csv,
)

#: Кодировки, которыми обычно оказывается сохранён CSV с русским текстом.
#: UTF-8 первым, следом то, во что сохраняет русский Excel.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")


def read_votes_file(picked, project: Project) -> ImportResult:
    """Разбирает выбранный в диалоге файл.

    :param picked: элемент результата `FilePicker.pick_files` — берём из него
                   байты, а если их нет (десктоп отдаёт только путь) — читаем
                   файл сами.
    """
    return parse_votes_text(_read_bytes(picked), project)


def parse_votes_text(data: bytes, project: Project) -> ImportResult:
    """Раскодирует байты и разбирает таблицу."""
    return parse_votes_csv(
        _decode(data),
        {d.name: d.id for d in project.districts},
        {p.name: p.id for p in project.parties},
    )


def read_support_file(picked, project: Project) -> SupportImportResult:
    """Разбирает выбранный файл с таблицей поддержки."""
    return parse_support_text(_read_bytes(picked), project)


def parse_support_text(data: bytes, project: Project) -> SupportImportResult:
    return parse_support_csv(
        _decode(data),
        {d.name: d.id for d in project.districts},
        {p.name: p.id for p in project.parties},
    )


def export_support_template(project: Project) -> bytes:
    """Заготовка таблицы поддержки под текущие округа, пункты и партии.

    Уже заведённые пункты выгружаются со своими очками, а у округов без
    пунктов остаётся пустая строка — чтобы было видно, что заполнить.
    Сохраняем с BOM, иначе Excel открывает кириллицу кракозябрами.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Округ", "Населённый пункт", *[p.name for p in project.parties]])
    for district in project.districts:
        if district.settlements:
            for settlement in district.settlements:
                writer.writerow([
                    district.name, settlement.name,
                    *[settlement.support.get(p.id, "") or "" for p in project.parties],
                ])
        else:
            writer.writerow([district.name, "", *[""] * len(project.parties)])
    return buffer.getvalue().encode("utf-8-sig")


def export_template(project: Project) -> bytes:
    """Пустая таблица под текущие округа и партии.

    Заголовок — названия партий ровно как в справочнике, строки — все округа
    карты: заполнить в Excel и загрузить обратно, ничего не сверяя руками.
    Сохраняем с BOM, иначе Excel открывает кириллицу кракозябрами.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Округ", *[p.name for p in project.parties]])
    for district in project.districts:
        writer.writerow([district.name, *[""] * len(project.parties)])
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
