"""Модель данных: партия, созыв, проект.

Проект целиком сериализуется в один JSON-файл — это и есть формат хранения
из ТЗ (раздел 5). Версия схемы записывается в файл, чтобы будущие изменения
формата можно было мигрировать, не ломая старые проекты.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEFAULT_TOTAL_SEATS = 120

#: Порядковые названия созывов. Дальше двадцатого — числом («21-й состав»).
ORDINALS = [
    "Первый", "Второй", "Третий", "Четвёртый", "Пятый",
    "Шестой", "Седьмой", "Восьмой", "Девятый", "Десятый",
    "Одиннадцатый", "Двенадцатый", "Тринадцатый", "Четырнадцатый", "Пятнадцатый",
    "Шестнадцатый", "Семнадцатый", "Восемнадцатый", "Девятнадцатый", "Двадцатый",
]

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def convocation_name(number: int) -> str:
    """«Третий состав» для 3, «21-й состав» для 21."""
    if 1 <= number <= len(ORDINALS):
        return f"{ORDINALS[number - 1]} состав"
    return f"{number}-й состав"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


@dataclass
class Party:
    """Партия из справочника. Живёт независимо от созывов: партия остаётся
    в списке, даже если в текущем составе не получила ни одного места."""

    id: str
    name: str
    color: str
    abbr: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color, "abbr": self.abbr}

    @staticmethod
    def from_dict(raw: dict) -> "Party":
        return Party(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            color=normalize_color(raw.get("color", "#7d7979")),
            abbr=str(raw.get("abbr", "")),
        )


@dataclass
class Convocation:
    """Созыв — снимок распределения мест.

    `fixed_at is None` означает открытый (редактируемый) созыв. Зафиксированные
    созывы по продуктовому решению тоже можно править — правки сохраняются в
    тот же созыв и нового не создают, — но именно `fixed_at` отделяет историю
    от текущего состава и задаёт порядок в списке.
    """

    id: str
    number: int
    name: str
    seats: dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    fixed_at: str | None = None

    @property
    def is_fixed(self) -> bool:
        return self.fixed_at is not None

    def used_seats(self) -> int:
        return sum(self.seats.values())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "seats": dict(self.seats),
            "createdAt": self.created_at,
            "fixedAt": self.fixed_at,
        }

    @staticmethod
    def from_dict(raw: dict) -> "Convocation":
        seats: dict[str, int] = {}
        for party_id, count in (raw.get("seats") or {}).items():
            try:
                value = int(count)
            except (TypeError, ValueError):
                continue
            if value > 0:
                seats[str(party_id)] = value
        return Convocation(
            id=str(raw["id"]),
            number=int(raw.get("number", 1)),
            name=str(raw.get("name") or convocation_name(int(raw.get("number", 1)))),
            seats=seats,
            created_at=str(raw.get("createdAt") or now_iso()),
            fixed_at=raw.get("fixedAt") or None,
        )


@dataclass
class Project:
    """Всё содержимое файла проекта: справочник партий и все созывы."""

    total_seats: int = DEFAULT_TOTAL_SEATS
    rows: int = 5
    parties: list[Party] = field(default_factory=list)
    convocations: list[Convocation] = field(default_factory=list)

    @staticmethod
    def empty(total_seats: int = DEFAULT_TOTAL_SEATS) -> "Project":
        """Новый проект — один пустой открытый созыв, партий ещё нет."""
        return Project(
            total_seats=total_seats,
            parties=[],
            convocations=[
                Convocation(id=new_id("c"), number=1, name=convocation_name(1))
            ],
        )

    def to_dict(self) -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "totalSeats": self.total_seats,
            "rows": self.rows,
            "parties": [p.to_dict() for p in self.parties],
            "convocations": [c.to_dict() for c in self.convocations],
        }

    @staticmethod
    def from_dict(raw: dict) -> "Project":
        if not isinstance(raw, dict):
            raise ValueError("Файл проекта повреждён: ожидался объект JSON.")

        total = int(raw.get("totalSeats") or DEFAULT_TOTAL_SEATS)
        if total < 1:
            raise ValueError("Файл проекта повреждён: некорректное число мест.")

        parties = [Party.from_dict(p) for p in raw.get("parties") or []]
        known = {p.id for p in parties}
        convocations = [Convocation.from_dict(c) for c in raw.get("convocations") or []]

        # Места, ссылающиеся на несуществующую партию, становятся
        # нераспределёнными: файл мог быть отредактирован вручную.
        for conv in convocations:
            conv.seats = {pid: n for pid, n in conv.seats.items() if pid in known}

        if not convocations:
            convocations = [Convocation(id=new_id("c"), number=1, name=convocation_name(1))]

        project = Project(total_seats=total, rows=int(raw.get("rows") or 5),
                          parties=parties, convocations=convocations)
        project.normalize()
        return project

    def normalize(self) -> None:
        """Приводит проект к инварианту: созывы упорядочены по номеру, и ровно
        один из них открыт (не зафиксирован) — последний."""
        self.convocations.sort(key=lambda c: c.number)
        for index, conv in enumerate(self.convocations):
            conv.number = index + 1
        open_ones = [c for c in self.convocations if not c.is_fixed]
        if len(open_ones) != 1:
            # Открыт всегда последний созыв, остальные — история.
            for conv in self.convocations[:-1]:
                if not conv.is_fixed:
                    conv.fixed_at = conv.created_at
            self.convocations[-1].fixed_at = None

    def remove_convocation(self, convocation_id: str) -> None:
        """Убирает созыв из истории; оставшиеся заново нумеруются по порядку.

        Если удалённое имя было автосгенерированным («Третий состав»), у
        сдвинувшихся созывов оно пересчитывается под новый номер — иначе
        порядковое слово в названии перестало бы совпадать с местом в
        списке. Названия, которые переименовал пользователь, не трогаем.
        Если удаляют текущий (открытый) созыв, `normalize()` возвращает к
        редактированию предыдущий — без открытого созыва проект не
        остаётся.
        """
        self.convocations = [c for c in self.convocations if c.id != convocation_id]
        self.convocations.sort(key=lambda c: c.number)
        for index, conv in enumerate(self.convocations):
            old_number = conv.number
            new_number = index + 1
            if conv.name == convocation_name(old_number):
                conv.name = convocation_name(new_number)
            conv.number = new_number
        self.normalize()

    # -- поиск --------------------------------------------------------------

    def party(self, party_id: str) -> Party | None:
        return next((p for p in self.parties if p.id == party_id), None)

    def convocation(self, convocation_id: str) -> Convocation | None:
        return next((c for c in self.convocations if c.id == convocation_id), None)

    @property
    def active_convocation(self) -> Convocation:
        """Открытый (редактируемый) созыв — последний по номеру."""
        return next(c for c in reversed(self.convocations) if not c.is_fixed)


def normalize_color(value: object) -> str:
    """Приводит цвет к виду `#rrggbb`. Принимает `#abc`, `abc`, `#AABBCC`."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Цвет не указан.")
    if not text.startswith("#"):
        text = "#" + text
    if len(text) == 4 and re.match(r"^#[0-9a-fA-F]{3}$", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not HEX_COLOR.match(text):
        raise ValueError(f"Некорректный HEX-цвет: «{value}». Ожидается вид #3A7CA5.")
    return text.lower()


def replace_party(party: Party, **changes) -> Party:
    return replace(party, **changes)
