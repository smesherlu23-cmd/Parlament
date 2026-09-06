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
#: Размер парламента по игровой карте — сумма мест всех округов (27 округов,
#: 124 мандата). Раньше было жёстко 120 из первой редакции ТЗ, но карта
#: главнее. Это только запасное значение: и новый проект, и открытый файл
#: берут число из самих округов — см. `Project.empty` и `sync_with_map`.
#: Пригождается лишь проектам, начатым до появления карты, у которых своих
#: округов нет вовсе.
DEFAULT_TOTAL_SEATS = 124

#: Запас очков популярности обычного населённого пункта. Дублируется в
#: `elections.SETTLEMENT_SUPPORT`; здесь — чтобы модель данных не зависела
#: от расчёта выборов.
DEFAULT_SUPPORT = 6

#: Порядковые названия созывов. Дальше двадцатого — числом («21-й состав»).
ORDINALS = [
    "Первый", "Второй", "Третий", "Четвёртый", "Пятый",
    "Шестой", "Седьмой", "Восьмой", "Девятый", "Десятый",
    "Одиннадцатый", "Двенадцатый", "Тринадцатый", "Четырнадцатый", "Пятнадцатый",
    "Шестнадцатый", "Семнадцатый", "Восемнадцатый", "Девятнадцатый", "Двадцатый",
]

#: Меньше двух партий — это не блок, а просто партия: коалиция из одного
#: участника показывала бы плёнку над самой собой и ничего не объясняла.
MIN_COALITION = 2

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
class Coalition:
    """Союз партий внутри одного созыва — блок, который голосует заодно.

    Своих мест у коалиции нет: её вес — это сумма мест участников, и меняется
    он сам собой, когда меняются их места. Поэтому здесь только состав, имя и
    цвет плёнки, которой блок накрыт на схеме зала: сквозь неё видно цвета
    самих партий, потому что коалиция — не новая партия, а то, как нынешние
    договорились между собой.

    Живёт в созыве, а не в проекте: с кем партия дружит — свойство созыва, и
    в следующем составе расклад бывает совсем другой. История должна помнить,
    кто с кем блокировался тогда, а не показывать нынешние союзы задним числом.
    """

    id: str
    name: str
    color: str
    #: `party_id` участников, в порядке добавления. Партия состоит не больше
    #: чем в одном блоке — иначе её места считались бы дважды.
    members: list[str] = field(default_factory=list)

    def seats(self, seats: dict[str, int]) -> int:
        """Вес блока при таком распределении мест."""
        return sum(seats.get(party_id, 0) for party_id in self.members)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color,
                "members": list(self.members)}

    @staticmethod
    def from_dict(raw: dict) -> "Coalition":
        return Coalition(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            color=normalize_color(raw.get("color", "#7d7979")),
            members=[str(m) for m in raw.get("members") or []],
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
    #: Разбор выборов — `{district_id: {party_id: PartyResult}}`. Единственный
    #: источник итогов округа: и веса для дележа мест, и проценты в разборе
    #: считаются из него (см. `service._weights`). Хранится целиком, потому
    #: что колебание случайно и второй раз не повторится — без него потом не
    #: понять, из чего сложился результат.
    #:
    #: Пустой словарь означает состав, набранный руками без выборов.
    results: dict[str, dict] = field(default_factory=dict)
    #: Блоки этого созыва — кто с кем договорился. Мест сами не несут, их
    #: вес складывается из мест участников.
    coalitions: list["Coalition"] = field(default_factory=list)

    @property
    def is_fixed(self) -> bool:
        return self.fixed_at is not None

    @property
    def has_election(self) -> bool:
        """Проводились ли по созыву выборы — от этого зависит, красится ли
        карта и набираются ли места руками.

        Смотрим на разбор, а не на розданные места: если поправки увели всех
        в ноль, мест не досталось никому, но выборы всё же состоялись.
        Считать такой созыв «без выборов» значило бы предлагать набрать места
        руками поверх уже сыгранного розыгрыша.
        """
        return bool(self.results)

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
            "results": {d: {p: r.to_dict() for p, r in per.items()}
                       for d, per in self.results.items() if per},
            "coalitions": [c.to_dict() for c in self.coalitions],
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

        from .elections import PartyResult

        # Старый формат (до перехода на проценты) хранил этот же разбор под
        # именем "rolls" и с другими полями (бросок кубика, а не доля
        # голосов), а веса для дележа мест — отдельным ключом "votes".
        # Читать их в новую форму бессмысленно: только состав (`seats`)
        # переживает переход, а выборы придётся сыграть заново — созыв
        # откроется как набранный руками, с уже проставленными местами.
        results: dict[str, dict] = {}
        for district_id, per_party in (raw.get("results") or {}).items():
            if not isinstance(per_party, dict):
                continue
            parsed = {str(pid): PartyResult.from_dict(r)
                     for pid, r in per_party.items() if isinstance(r, dict)}
            if parsed:
                results[str(district_id)] = parsed

        return Convocation(
            id=str(raw["id"]),
            number=int(raw.get("number", 1)),
            name=str(raw.get("name") or convocation_name(int(raw.get("number", 1)))),
            seats=seats,
            created_at=str(raw.get("createdAt") or now_iso()),
            fixed_at=raw.get("fixedAt") or None,
            results=results,
            coalitions=[Coalition.from_dict(c) for c in raw.get("coalitions") or []
                        if isinstance(c, dict) and c.get("id")],
        )


@dataclass
class Settlement:
    """Населённый пункт внутри округа — или город, общий на несколько округов.

    Несёт очки неформальной популярности, которые игроки делят между
    партиями. Из них складывается модификатор поддержки на выборах.

    Запас очков у пунктов разный, поэтому он хранится здесь, а не берётся из
    одной константы: в обычном селе их шесть, а у города вдвое больше —
    см. `elections`. Тот же класс используется и для городской копилки в
    `Project.cities`: несколько избирательных округов одного города (скажем,
    Саттмалвик-порт и -центр) делят один такой объект вместо того, чтобы
    каждый заводил себе отдельный.
    """

    id: str
    name: str
    #: `{party_id: очки}`. Партии без очков в словаре не хранятся.
    support: dict[str, int] = field(default_factory=dict)
    #: Сколько очков всего можно раздать в этом пункте.
    capacity: int = DEFAULT_SUPPORT

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "capacity": self.capacity,
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
        try:
            capacity = int(raw.get("capacity") or DEFAULT_SUPPORT)
        except (TypeError, ValueError):
            capacity = DEFAULT_SUPPORT
        return Settlement(id=str(raw["id"]), name=str(raw.get("name", "")),
                          support=support, capacity=max(0, capacity))


@dataclass
class District:
    """Избирательный округ с карты: сколько мест разыгрывает и как называется.

    Где округ нарисован, здесь не хранится: и границы, и точка подписи лежат
    в `district_geometry` и находятся по `code`. Раньше копия центра лежала
    ещё и тут — и после уточнения геометрии копия в старых файлах проекта
    оставалась от прежней карты.

    `region` — только подпись для группировки в списке, на расчёт не влияет.
    """

    id: str
    name: str
    seats: int
    region: str = ""
    #: Номер округа на игровой карте. По нему в `district_geometry` находятся
    #: полигон и точка подписи; 0 — округ без нарисованных границ (такое
    #: бывает у проектов, заведённых до появления карты).
    code: int = 0
    #: Населённые пункты округа. Заводятся пользователем на экране поддержки:
    #: в присланной карте их нет, а модификатор считается по ним. У
    #: городского округа список всегда пуст — его очки не свои, а общие на
    #: весь город, см. `Project.cities`.
    settlements: list[Settlement] = field(default_factory=list)
    #: Дописаны ли сельские пункты с карты хотя бы раз. Пока False,
    #: `sync_with_map` при каждом открытии файла подсаживает недостающие —
    #: это разовая миграция для проектов, начатых до появления пунктов на
    #: карте. Как только это случилось, флаг встаёт и больше не сбрасывается:
    #: иначе удалённый или переименованный пользователем пункт с картовым
    #: именем при следующем запуске тихо возвращался бы обратно.
    settlements_synced: bool = False

    def settlement(self, settlement_id: str) -> "Settlement | None":
        return next((s for s in self.settlements if s.id == settlement_id), None)

    def support_points(self, party_id: str) -> int:
        """Сумма очков партии по всем НП округа."""
        return sum(s.support.get(party_id, 0) for s in self.settlements)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "seats": self.seats,
            "region": self.region, "code": self.code,
            "settlements": [s.to_dict() for s in self.settlements],
            "settlementsSynced": self.settlements_synced,
        }

    @staticmethod
    def from_dict(raw: dict) -> "District":
        try:
            code = int(raw.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        # Ключи x/y в старых файлах игнорируем: точка подписи считается по
        # геометрии, и хранившаяся копия успела от неё отстать.
        return District(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            seats=max(0, int(raw.get("seats") or 0)),
            region=str(raw.get("region", "")),
            code=code,
            settlements=[Settlement.from_dict(s) for s in raw.get("settlements") or []],
            # Отсутствующий ключ — файл старше самого поля, и его сельские
            # пункты миграцию ещё не проходили; ровно то состояние, для
            # которого миграция и задумана.
            settlements_synced=bool(raw.get("settlementsSynced", False)),
        )


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
    #: Городские копилки очков — по одной на метрополию (Саттмалвик,
    #: Гаффинсвик, Триединсборг, Нивенсхолл), а не по одной на округ. Город
    #: обычно делится на несколько избирательных округов (Саттмалвик-порт,
    #: -центр...), но очки популярности в нём общие — см. `district_support`.
    cities: list[Settlement] = field(default_factory=list)

    @staticmethod
    def empty() -> "Project":
        """Новый проект — округа с карты, один пустой созыв, партий ещё нет.

        Общее число мест берётся не из константы, а из суммы округов: карта —
        источник истины, и разъехаться эти числа не должны. Задать его извне
        нельзя: при следующем открытии файла `sync_with_map` всё равно
        пересчитает его по округам.
        """
        districts = default_districts()
        return Project(
            total_seats=sum(d.seats for d in districts),
            parties=[],
            districts=districts,
            cities=default_cities(),
            convocations=[
                Convocation(id=new_id("c"), number=1, name=convocation_name(1))
            ],
        )

    def district(self, district_id: str) -> "District | None":
        return next((d for d in self.districts if d.id == district_id), None)

    def city(self, region: str) -> "Settlement | None":
        """Городская копилка метрополии — по названию региона округа."""
        return next((c for c in self.cities if c.name == region), None)

    def city_by_id(self, city_id: str) -> "Settlement | None":
        return next((c for c in self.cities if c.id == city_id), None)

    def district_support(self, district: "District", party_id: str) -> int:
        """Очки партии в округе — основа для её базовой доли голосов.

        У городского округа своих очков нет: они общие на весь город, и
        смотреть надо в копилку его метрополии, а не в пустой `settlements`.
        """
        from .district_seed import is_city

        if is_city(district.code):
            city = self.city(district.region)
            return city.support.get(party_id, 0) if city else 0
        return district.support_points(party_id)

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
            "cities": [c.to_dict() for c in self.cities],
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
        cities = [Settlement.from_dict(c) for c in raw.get("cities") or []]

        # Места и итоги, ссылающиеся на несуществующую партию или округ,
        # отбрасываются: файл мог быть отредактирован вручную. Округ, из
        # которого кто-то выпал, пересчитывается — иначе его доли перестали
        # бы давать в сумме сотню, см. `elections.renormalize`.
        from .elections import renormalize

        for conv in convocations:
            conv.seats = {pid: n for pid, n in conv.seats.items() if pid in known}
            trimmed = {
                did: {pid: r for pid, r in per.items() if pid in known}
                for did, per in conv.results.items()
                if did in known_districts
            }
            conv.results = {did: renormalize(per) for did, per in trimmed.items() if per}
            # Блок без двух живых участников — уже не блок: партию могли
            # удалить, а файл — поправить руками.
            for coalition in conv.coalitions:
                coalition.members = [pid for pid in coalition.members if pid in known]
            conv.coalitions = [c for c in conv.coalitions if len(c.members) >= MIN_COALITION]

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
                          recent_colors=recent_colors, districts=districts,
                          cities=cities)
        project.sync_with_map()
        project.normalize()
        return project

    def sync_with_map(self) -> None:
        """Подтягивает округа, их пункты и городские копилки из игровой карты.

        Округа — не пользовательские данные, а карта: их названия, мандаты и
        состав сёл задаёт игра, и переименовать округ в программе нельзя.
        Значит, уточнение карты должно доезжать и до уже начатых проектов —
        иначе у игрока, начавшего партию раньше, навсегда осталась бы прежняя
        разметка (а по ней он ещё и раздаёт очки поддержки).

        Розданные очки при этом не трогаются: пункт узнаётся по названию, а
        заведённые пользователем сверх карты остаются как есть — вдруг он
        добавил хутор, которого на карте нет.

        Сельские пункты дописываются только один раз за округ (пока не
        встал `settlements_synced`) — иначе удалённый или переименованный
        пользователем картовый пункт тихо возвращался бы обратно при каждом
        следующем открытии файла.
        """
        if not self.districts:
            return          # проект начат до карты — досочинять ему нечего

        from .district_seed import SEED_DISTRICTS, is_city

        by_code = {code: (name, seats, region, places)
                   for code, name, seats, region, places in SEED_DISTRICTS}
        # Округа появились в программе раньше, чем номера на карте: в файлах
        # тех сборок `code` нет вовсе, а без него округ не с чем связать —
        # ни границ, ни подписи, и карта у такого проекта оставалась пустой.
        # Названия с тех пор не менялись, поэтому номер восстанавливается
        # по имени.
        by_name = {_district_key(name): code
                   for code, name, _seats, _region, _places in SEED_DISTRICTS}

        self._ensure_cities()

        for district in self.districts:
            if not district.code:
                district.code = by_name.get(_district_key(district.name), 0)
            fresh = by_code.get(district.code)
            if fresh is None:
                continue
            district.name, district.seats, district.region, places = fresh
            if is_city(district.code):
                # Раньше у каждого городского округа была своя копилка —
                # теперь она общая на весь город. Уже розданные игроками
                # очки не выбрасываем, а переносим в общую: иначе обновление
                # программы само стёрло бы то, что накопили за игру.
                self._absorb_into_city(district)
                district.settlements = []
                district.settlements_synced = True
            elif not district.settlements_synced:
                _merge_settlements(district, places)
                district.settlements_synced = True

        # Размер палаты задаётся картой: разъезжаться этим числам нельзя.
        self.total_seats = sum(d.seats for d in self.districts)

    def _ensure_cities(self) -> None:
        """Заводит копилки для новых городов карты, не трогая существующие."""
        from .district_seed import city_regions
        from .elections import CITY_SUPPORT

        have = {c.name for c in self.cities}
        for region in city_regions():
            if region not in have:
                self.cities.append(Settlement(id=new_id("s"), name=region,
                                              capacity=CITY_SUPPORT))
        for city in self.cities:
            city.capacity = CITY_SUPPORT

    def _absorb_into_city(self, district: "District") -> None:
        """Переносит очки старой личной копилки округа в общую городскую."""
        if not district.settlements:
            return
        city = self.city(district.region)
        if city is None:
            return
        for legacy in district.settlements:
            for party_id, points in legacy.support.items():
                city.support[party_id] = city.support.get(party_id, 0) + points

    def normalize(self) -> None:
        """Приводит проект к инварианту: созывы упорядочены по номеру, и ровно
        один из них открыт (не зафиксирован) — последний.

        Проверяем не только «сколько открытых», но и «тот ли открыт»: файл
        правится руками, и открытый созыв в середине истории ломал бы
        нумерацию следующего — новый созыв получал бы номер, который уже
        занят.
        """
        if not self.convocations:
            self.convocations = [
                Convocation(id=new_id("c"), number=1, name=convocation_name(1))
            ]
        self.convocations.sort(key=lambda c: c.number)
        for index, conv in enumerate(self.convocations):
            conv.number = index + 1

        last = self.convocations[-1]
        for conv in self.convocations[:-1]:
            if not conv.is_fixed:
                conv.fixed_at = conv.created_at
        last.fixed_at = None

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


def _merge_settlements(district: District, places: tuple[str, ...]) -> None:
    """Дописывает сельскому округу пункты с карты, не трогая розданные очки.

    Пункт узнаётся по названию: идентификаторы у каждого проекта свои, а
    названия — общие, они и есть карта. Для городских округов не зовётся —
    их очки не свои, а общие на весь город, см. `Project._absorb_into_city`.
    """
    from .elections import SETTLEMENT_SUPPORT

    existing = {_district_key(s.name): s for s in district.settlements}

    for name in places:
        settlement = existing.get(_district_key(name))
        if settlement is None:
            district.settlements.append(
                Settlement(id=new_id("s"), name=name, capacity=SETTLEMENT_SUPPORT))
        else:
            settlement.name = name
            settlement.capacity = SETTLEMENT_SUPPORT


def _district_key(name: str) -> str:
    """Ключ сопоставления округа по названию — без регистра и лишних пробелов."""
    return " ".join(str(name).split()).casefold()


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
    """Округа игровой карты вместе с их сельскими населёнными пунктами.

    Пункты заводятся сразу: они есть на карте, все до одного известны, и
    вбивать их руками в двадцати семи округах — работа на пустом месте.
    Городские округа своих пунктов не получают — их очки общие на весь
    город, см. `default_cities`.

    Импорт внутри функции, а не наверху модуля: `district_seed` — это данные
    конкретной игры, а `model` описывает формат вообще, и обратной зависимости
    у него быть не должно.
    """
    from .district_seed import SEED_DISTRICTS

    return [
        District(id=new_id("d"), name=name, seats=seats, region=region, code=code,
                 settlements=seed_settlements(places), settlements_synced=True)
        for code, name, seats, region, places in SEED_DISTRICTS
    ]


def seed_settlements(places: tuple[str, ...] | None) -> list[Settlement]:
    """Сельские населённые пункты округа по карте — пусто для городского."""
    from .elections import SETTLEMENT_SUPPORT

    if places is None:
        return []
    return [Settlement(id=new_id("s"), name=name, capacity=SETTLEMENT_SUPPORT)
            for name in places]


def default_cities() -> list[Settlement]:
    """Городские копилки очков — по одной на метрополию, а не на округ.

    Импорт внутри функции по тем же причинам, что и в `default_districts`.
    """
    from .district_seed import city_regions
    from .elections import CITY_SUPPORT

    return [Settlement(id=new_id("s"), name=region, capacity=CITY_SUPPORT)
            for region in city_regions()]
