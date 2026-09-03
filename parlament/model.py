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

SCHEMA_VERSION = 2
#: Размер парламента по игровой карте — сумма мест всех округов. Раньше было
#: жёстко 120 (из первой редакции ТЗ), но карта задаёт 147, и она главнее.
#: Новые проекты берут это число из самих округов, см. `Project.empty`.
DEFAULT_TOTAL_SEATS = 147

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
    #: Вес партий по округам — `{district_id: {party_id: число}}`. Из них
    #: сервис пересобирает `seats`. Пустой словарь означает состав, набранный
    #: руками без выборов — так работают старые проекты и ручная правка.
    votes: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Разбор розыгрыша — `{district_id: {party_id: PartyRoll}}`. Хранится
    #: рядом с весами, потому что бросок случаен и второй раз не повторится:
    #: без него потом не понять, из чего сложился результат.
    rolls: dict[str, dict] = field(default_factory=dict)

    @property
    def is_fixed(self) -> bool:
        return self.fixed_at is not None

    @property
    def has_election(self) -> bool:
        """Есть ли по созыву данные выборов — от этого зависит, можно ли
        красить карту и показывать расклад по округам."""
        return any(self.votes.values())

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
            "votes": {d: dict(v) for d, v in self.votes.items() if v},
            "rolls": {d: {p: r.to_dict() for p, r in per.items()}
                      for d, per in self.rolls.items() if per},
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

        votes: dict[str, dict[str, float]] = {}
        for district_id, per_party in (raw.get("votes") or {}).items():
            if not isinstance(per_party, dict):
                continue
            cleaned: dict[str, float] = {}
            for party_id, count in per_party.items():
                try:
                    value = float(count)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    cleaned[str(party_id)] = value
            if cleaned:
                votes[str(district_id)] = cleaned

        from .elections import PartyRoll

        rolls: dict[str, dict] = {}
        for district_id, per_party in (raw.get("rolls") or {}).items():
            if not isinstance(per_party, dict):
                continue
            parsed = {str(pid): PartyRoll.from_dict(r)
                      for pid, r in per_party.items() if isinstance(r, dict)}
            if parsed:
                rolls[str(district_id)] = parsed

        return Convocation(
            id=str(raw["id"]),
            number=int(raw.get("number", 1)),
            name=str(raw.get("name") or convocation_name(int(raw.get("number", 1)))),
            seats=seats,
            created_at=str(raw.get("createdAt") or now_iso()),
            fixed_at=raw.get("fixedAt") or None,
            votes=votes,
            rolls=rolls,
        )


@dataclass
class Settlement:
    """Населённый пункт внутри округа.

    Несёт очки неформальной популярности, которые игроки делят между
    партиями (сколько всего очков на пункт — см. `elections.SETTLEMENT_SUPPORT`).
    Из них складывается модификатор поддержки на выборах.
    """

    id: str
    name: str
    #: `{party_id: очки}`. Партии без очков в словаре не хранятся.
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "support": {k: v for k, v in self.support.items() if v > 0}}

    @staticmethod
    def from_dict(raw: dict) -> "Settlement":
        support: dict[str, int] = {}
        for party_id, points in (raw.get("support") or {}).items():
            try:
                value = int(points)
            except (TypeError, ValueError):
                continue
            if value > 0:
                support[str(party_id)] = value
        return Settlement(id=str(raw["id"]), name=str(raw.get("name", "")),
                          support=support)


@dataclass
class District:
    """Избирательный округ с карты: сколько мест разыгрывает и где нарисован.

    `x` и `y` — центр округа в долях от размера карты (0..1), а не в пикселях:
    карта на разных экранах тянется, и подпись должна ехать вместе с ней.
    `region` — только подпись для группировки в списке, на расчёт не влияет.
    """

    id: str
    name: str
    seats: int
    region: str = ""
    x: float = 0.5
    y: float = 0.5
    #: Номер округа на игровой карте. По нему в `district_geometry` находится
    #: полигон; 0 — округ без нарисованных границ (такое бывает у проектов,
    #: заведённых до появления карты).
    code: int = 0
    #: Населённые пункты округа. Заводятся пользователем на экране поддержки:
    #: в присланной карте их нет, а модификатор считается по ним.
    settlements: list[Settlement] = field(default_factory=list)

    def settlement(self, settlement_id: str) -> "Settlement | None":
        return next((s for s in self.settlements if s.id == settlement_id), None)

    def support_points(self, party_id: str) -> int:
        """Сумма очков партии по всем НП округа."""
        return sum(s.support.get(party_id, 0) for s in self.settlements)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "seats": self.seats,
            "region": self.region, "x": self.x, "y": self.y, "code": self.code,
            "settlements": [s.to_dict() for s in self.settlements],
        }

    @staticmethod
    def from_dict(raw: dict) -> "District":
        try:
            code = int(raw.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        return District(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            seats=max(0, int(raw.get("seats") or 0)),
            region=str(raw.get("region", "")),
            x=_clamp_unit(raw.get("x", 0.5)),
            y=_clamp_unit(raw.get("y", 0.5)),
            code=code,
            settlements=[Settlement.from_dict(s) for s in raw.get("settlements") or []],
        )


def _clamp_unit(value: object) -> float:
    """Доля 0..1; мусор из руками поправленного файла превращается в центр."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, number))


@dataclass
class Project:
    """Всё содержимое файла проекта: справочник партий и все созывы."""

    total_seats: int = DEFAULT_TOTAL_SEATS
    rows: int = 5
    parties: list[Party] = field(default_factory=list)
    convocations: list[Convocation] = field(default_factory=list)
    #: Свои цвета, подобранные вручную в диалоге партии (свежий слева) —
    #: чтобы для похожих партий подряд не открывать подбор заново.
    recent_colors: list[str] = field(default_factory=list)
    #: Избирательные округа с карты. Пустой список — проект без выборов по
    #: округам: места тогда вводятся руками, как раньше.
    districts: list[District] = field(default_factory=list)

    @staticmethod
    def empty(total_seats: int | None = None) -> "Project":
        """Новый проект — округа с карты, один пустой созыв, партий ещё нет.

        Общее число мест по умолчанию берётся не из константы, а из суммы
        округов: карта — источник истины, и разъехаться эти числа не должны.
        """
        districts = default_districts()
        return Project(
            total_seats=total_seats if total_seats is not None
                        else sum(d.seats for d in districts),
            parties=[],
            districts=districts,
            convocations=[
                Convocation(id=new_id("c"), number=1, name=convocation_name(1))
            ],
        )

    def district(self, district_id: str) -> "District | None":
        return next((d for d in self.districts if d.id == district_id), None)

    @property
    def district_seats(self) -> dict[str, int]:
        """`{district_id: мест}` — в таком виде это ждёт расчёт выборов."""
        return {d.id: d.seats for d in self.districts}

    def to_dict(self) -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "totalSeats": self.total_seats,
            "rows": self.rows,
            "parties": [p.to_dict() for p in self.parties],
            "convocations": [c.to_dict() for c in self.convocations],
            "recentColors": list(self.recent_colors),
            "districts": [d.to_dict() for d in self.districts],
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

        # Округа: если их в файле нет — это проект, созданный до появления
        # карты. Досочинять ему округа нельзя, иначе разъедется общее число
        # мест, набранное руками.
        districts = [District.from_dict(d) for d in raw.get("districts") or []]
        known_districts = {d.id for d in districts}

        # Места и голоса, ссылающиеся на несуществующую партию или округ,
        # отбрасываются: файл мог быть отредактирован вручную.
        for conv in convocations:
            conv.seats = {pid: n for pid, n in conv.seats.items() if pid in known}
            conv.votes = {
                did: {pid: n for pid, n in per_party.items() if pid in known}
                for did, per_party in conv.votes.items()
                if did in known_districts
            }
            conv.votes = {did: v for did, v in conv.votes.items() if v}
            conv.rolls = {
                did: {pid: r for pid, r in per.items() if pid in known}
                for did, per in conv.rolls.items()
                if did in known_districts
            }
            conv.rolls = {did: r for did, r in conv.rolls.items() if r}

        if not convocations:
            convocations = [Convocation(id=new_id("c"), number=1, name=convocation_name(1))]

        recent_colors: list[str] = []
        for value in raw.get("recentColors") or []:
            try:
                recent_colors.append(normalize_color(value))
            except ValueError:
                continue

        project = Project(total_seats=total, rows=int(raw.get("rows") or 5),
                          parties=parties, convocations=convocations,
                          recent_colors=recent_colors, districts=districts)
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


def default_districts() -> list[District]:
    """Округа с игровой карты — 27 штук на 147 мест.

    Импорт внутри функции, а не наверху модуля: `district_seed` — это данные
    конкретной игры, а `model` описывает формат вообще, и обратной зависимости
    у него быть не должно.
    """
    from .district_geometry import DISTRICT_CENTRES
    from .district_seed import SEED_DISTRICTS

    districts = []
    for code, name, seats, region in SEED_DISTRICTS:
        # Точка подписи — центр тяжести полигона, а не отдельное число:
        # так она не может разъехаться с нарисованными границами.
        x, y = DISTRICT_CENTRES.get(code, (0.5, 0.5))
        districts.append(District(id=new_id("d"), name=name, seats=seats,
                                  region=region, x=x, y=y, code=code))
    return districts
