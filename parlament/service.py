"""Операции над проектом — единственное место, где меняются данные.

Сервис отвечает за валидацию (раздел 8 ТЗ: нельзя отрицательное число мест,
нельзя превысить общее число мест) и за автосохранение: после каждой
успешной мутации проект пишется в текущий файл, поэтому закрытие окна
никогда не теряет работу.
"""

from __future__ import annotations

import random
from pathlib import Path

from . import district_seed, elections, store
from .model import (
    Convocation,
    District,
    Settlement,
    default_districts,
    Party,
    Project,
    convocation_name,
    new_id,
    normalize_color,
    now_iso,
)

MAX_NAME_LENGTH = 120
MAX_ABBR_LENGTH = 12
#: Сколько последних вручную подобранных цветов хранить в проекте.
RECENT_COLORS_LIMIT = 10


class ValidationError(Exception):
    """Ввод пользователя не прошёл проверку. Текст показывается в интерфейсе."""


def _same_name(left: str, right: str) -> bool:
    """Одно и то же ли это название — без учёта регистра и лишних пробелов."""
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


class ParlamentService:
    """Держит текущий проект в памяти и синхронизирует его с файлом."""

    def __init__(self, default_path: str | Path):
        self.default_path = Path(default_path)
        self.path: Path = self.default_path
        self.project: Project = Project.empty()
        self._loaded_from_disk = False

    # -- жизненный цикл проекта --------------------------------------------

    def bootstrap(self) -> None:
        """Стартовая загрузка: открываем прошлый проект, если он есть."""
        if self.default_path.exists():
            self.project = store.load(self.default_path)
            self._loaded_from_disk = True
        else:
            self.project = Project.empty()
            self._loaded_from_disk = False
        self.path = self.default_path

    def new_project(self) -> None:
        self.project = Project.empty()
        self.path = self.default_path
        self._loaded_from_disk = False
        self._persist()

    def open_project(self, path: str) -> None:
        self.project = store.load(path)
        self.path = Path(path)
        self._loaded_from_disk = True

    def save_project_as(self, path: str) -> None:
        target = Path(path)
        store.save(self.project, target)
        self.path = target
        self._loaded_from_disk = True

    def save_project(self) -> None:
        self._persist()

    def _persist(self) -> None:
        store.save(self.project, self.path)
        self._loaded_from_disk = True

    # -- справочник партий --------------------------------------------------

    def create_party(self, name: str, color: str, abbr: str = "") -> Party:
        party = Party(
            id=new_id("p"),
            name=self._clean_name(name),
            color=self._clean_color(color),
            abbr=self._clean_abbr(abbr),
        )
        self.project.parties.append(party)
        self._persist()
        return party

    def update_party(self, party_id: str, name: str, color: str, abbr: str = "") -> Party:
        party = self._require_party(party_id)
        party.name = self._clean_name(name)
        party.color = self._clean_color(color)
        party.abbr = self._clean_abbr(abbr)
        self._persist()
        return party

    def delete_party(self, party_id: str) -> None:
        """Удаляет партию из справочника — вместе со всеми её следами.

        Мест в созывах мало: за партией остаются ещё очки популярности в
        населённых пунктах и её доля в разыгранных округах. Очки особенно
        важно убрать: запас пункта общий, и очки исчезнувшей партии навсегда
        заняли бы часть этого запаса — отдать их кому-то другому стало бы
        нельзя.

        Созывы, где места посчитаны выборами, пересчитываются: округа
        делятся заново уже без этой партии.
        """
        party = self._require_party(party_id)
        self.project.parties = [p for p in self.project.parties if p.id != party.id]

        for district in self.project.districts:
            for settlement in district.settlements:
                settlement.support.pop(party.id, None)
        for city in self.project.cities:
            city.support.pop(party.id, None)

        for conv in self.project.convocations:
            conv.seats.pop(party.id, None)
            if not conv.has_election:
                continue
            # Разбор чистим наравне с голосами: округ, где все ушли в ноль,
            # голосов не имеет, но разбор по нему хранится — и партия
            # осталась бы в нём призраком.
            conv.votes = {did: {pid: n for pid, n in per.items() if pid != party.id}
                          for did, per in conv.votes.items()}
            conv.votes = {did: per for did, per in conv.votes.items() if per}
            conv.rolls = {did: {pid: r for pid, r in per.items() if pid != party.id}
                          for did, per in conv.rolls.items()}
            conv.rolls = {did: per for did, per in conv.rolls.items() if per}
            self._recount(conv)

        self._persist()

    def party_usage(self, party_id: str) -> list[dict]:
        """Где партия участвует — для предупреждения перед удалением."""
        self._require_party(party_id)
        return [
            {"convocationId": c.id, "convocationName": c.name, "seats": c.seats[party_id]}
            for c in self.project.convocations
            if c.seats.get(party_id, 0) > 0
        ]

    def party_footprint(self, party_id: str) -> dict:
        """Что пропадёт вместе с партией — для предупреждения перед удалением.

        Мест в созывах мало: за партией стоят ещё очки популярности, копившиеся
        всю игру, и её доля в разыгранных округах. Уносить это молча нельзя.
        """
        self._require_party(party_id)
        points = settlements = 0
        for district in self.project.districts:
            for settlement in district.settlements:
                value = settlement.support.get(party_id, 0)
                if value:
                    points += value
                    settlements += 1
        for city in self.project.cities:
            value = city.support.get(party_id, 0)
            if value:
                points += value
                settlements += 1
        rolled = sum(1 for conv in self.project.convocations
                     for per in conv.rolls.values() if party_id in per)
        return {
            "convocations": self.party_usage(party_id),
            "supportPoints": points,
            "settlements": settlements,
            "rolledDistricts": rolled,
            "recountedConvocations": sum(
                1 for conv in self.project.convocations
                if conv.has_election and any(party_id in per for per in conv.rolls.values())
            ),
        }

    # -- распределение мест -------------------------------------------------

    def set_seats(self, convocation_id: str, party_id: str, seats: int) -> Convocation:
        """Ставит партии конкретное число мест в созыве."""
        conv = self._require_convocation(convocation_id)
        self._require_party(party_id)
        self._require_manual(conv)

        count = self._clean_seat_count(seats)
        others = sum(n for pid, n in conv.seats.items() if pid != party_id)
        if others + count > self.project.total_seats:
            raise ValidationError(
                f"Всего мест — {self.project.total_seats}. "
                f"Остальным партиям уже отдано {others}, "
                f"этой партии можно дать не больше {self.project.total_seats - others}."
            )

        if count == 0:
            conv.seats.pop(party_id, None)
        else:
            conv.seats[party_id] = count
        self._persist()
        return conv

    def set_all_seats(self, convocation_id: str, seats: dict[str, int]) -> Convocation:
        """Заменяет всё распределение созыва разом (импорт, отмена, сброс)."""
        conv = self._require_convocation(convocation_id)
        self._require_manual(conv)
        cleaned: dict[str, int] = {}
        for party_id, value in (seats or {}).items():
            self._require_party(party_id)
            count = self._clean_seat_count(value)
            if count:
                cleaned[party_id] = count
        total = sum(cleaned.values())
        if total > self.project.total_seats:
            raise ValidationError(
                f"Распределено {total} мест из {self.project.total_seats} — "
                f"это на {total - self.project.total_seats} больше, чем есть."
            )
        conv.seats = cleaned
        self._persist()
        return conv

    def reset_seats(self, convocation_id: str) -> Convocation:
        return self.set_all_seats(convocation_id, {})

    def _require_manual(self, conv: Convocation) -> None:
        """Не даёт править руками состав, посчитанный выборами.

        Иначе зал разошёлся бы с картой: округа остались бы покрашены и
        расписаны по партиям, а число мест в зале — уже другое. Чтобы
        вернуться к ручному набору, выборы сбрасываются целиком.
        """
        if conv.has_election:
            raise ValidationError(
                "Места в этом созыве посчитаны выборами. Чтобы набирать их "
                "руками, сначала сбросьте выборы."
            )

    # -- созывы -------------------------------------------------------------

    def rename_convocation(self, convocation_id: str, name: str) -> Convocation:
        conv = self._require_convocation(convocation_id)
        conv.name = self._clean_name(name)
        self._persist()
        return conv

    def fix_convocation(self, name: str | None = None) -> Convocation:
        """Фиксирует текущий созыв и открывает следующий, пустой.

        Возвращает новый созыв — интерфейс сразу переключается на него.
        """
        current = self.project.active_convocation
        current.fixed_at = now_iso()

        number = current.number + 1
        fresh = Convocation(
            id=new_id("c"),
            number=number,
            name=self._clean_name(name) if name and name.strip() else convocation_name(number),
        )
        self.project.convocations.append(fresh)
        self._persist()
        return fresh

    def next_convocation_name(self) -> str:
        """Подсказка для диалога фиксации — имя следующего созыва."""
        return convocation_name(self.project.active_convocation.number + 1)

    def delete_convocation(self, convocation_id: str) -> Convocation:
        """Удаляет созыв из истории — кроме случая, когда он единственный.

        Если удаляют текущий (открытый) созыв, для правки снова открывается
        предыдущий по времени: без открытого созыва проект не остаётся.
        """
        if len(self.project.convocations) <= 1:
            raise ValidationError("Нельзя удалить единственный созыв.")
        self._require_convocation(convocation_id)

        self.project.remove_convocation(convocation_id)
        self._persist()
        return self.project.active_convocation

    # -- выборы по округам ----------------------------------------------------

    def adopt_map_districts(self) -> None:
        """Заводит в проекте округа игровой карты.

        Нужно проектам, начатым до появления карты: сами они округов не
        получают (см. `Project.from_dict`), иначе набранные вручную места
        разъехались бы с новым размером палаты у всех разом. Здесь это
        осознанный шаг пользователя, поэтому и размер палаты подтягивается
        под сумму округов.

        Уже распределённые места не трогаем: они остаются как есть, просто
        часть палаты становится нераспределённой.
        """
        if self.project.districts:
            raise ValidationError("Округа в проекте уже есть.")
        self.project.districts = default_districts()
        self.project.total_seats = sum(d.seats for d in self.project.districts)
        self._persist()

    # -- населённые пункты и поддержка ---------------------------------------

    def add_settlement(self, district_id: str, name: str) -> Settlement:
        """Заводит в округе пункт сверх тех, что пришли с карты.

        Два пункта с одним названием в округе не заводятся: таблица поддержки
        сопоставляет их по имени, и второй «Судурей» стал бы неразличим с
        первым — импорт молча писал бы очки не туда.
        """
        district = self._require_district(district_id)
        if district_seed.is_city(district.code):
            raise ValidationError(
                "У городского округа нет своих населённых пунктов — очки "
                "делятся через сам город, на экране поддержки.")
        clean = self._clean_name(name)
        if any(_same_name(s.name, clean) for s in district.settlements):
            raise ValidationError(
                f"В округе «{district.name}» уже есть «{clean}».")
        settlement = Settlement(id=new_id("s"), name=clean)
        district.settlements.append(settlement)
        self._persist()
        return settlement

    def rename_settlement(self, district_id: str, settlement_id: str,
                          name: str) -> Settlement:
        district = self._require_district(district_id)
        settlement = self._require_settlement(district_id, settlement_id)
        clean = self._clean_name(name)
        if any(_same_name(s.name, clean) for s in district.settlements
               if s.id != settlement_id):
            raise ValidationError(
                f"В округе «{district.name}» уже есть «{clean}».")
        settlement.name = clean
        self._persist()
        return settlement

    def delete_settlement(self, district_id: str, settlement_id: str) -> None:
        district = self._require_district(district_id)
        self._require_settlement(district_id, settlement_id)
        district.settlements = [s for s in district.settlements if s.id != settlement_id]
        self._persist()

    def set_support(self, district_id: str, settlement_id: str,
                    party_id: str, points: int) -> Settlement:
        """Ставит партии очки популярности в сельском населённом пункте.

        Сумма по пункту ограничена его запасом — шесть очков. Раздать
        больше нельзя: иначе поддержка перестала бы быть дележом общего
        запаса. Для городского округа очки общие на весь город — см.
        `set_city_support`.
        """
        settlement = self._require_settlement(district_id, settlement_id)
        self._require_party(party_id)

        value = self._clean_support(points)
        others = sum(n for pid, n in settlement.support.items() if pid != party_id)
        if others + value > settlement.capacity:
            raise ValidationError(
                f"В «{settlement.name}» всего {settlement.capacity} очков "
                f"популярности. Другим партиям уже отдано {others}, "
                f"этой можно дать не больше {settlement.capacity - others}."
            )

        if value == 0:
            settlement.support.pop(party_id, None)
        else:
            settlement.support[party_id] = value
        self._persist()
        return settlement

    def set_city_support(self, city_id: str, party_id: str, points: int) -> Settlement:
        """Ставит партии очки популярности в городе — общие на все его округа.

        В отличие от сельского пункта, город не привязан к одному округу:
        несколько избирательных округов метрополии (Саттмалвик-порт,
        -центр...) делят один и тот же запас.
        """
        city = self._require_city(city_id)
        self._require_party(party_id)

        value = self._clean_support(points)
        others = sum(n for pid, n in city.support.items() if pid != party_id)
        if others + value > city.capacity:
            raise ValidationError(
                f"В городе «{city.name}» всего {city.capacity} очков "
                f"популярности. Другим партиям уже отдано {others}, "
                f"этой можно дать не больше {city.capacity - others}."
            )

        if value == 0:
            city.support.pop(party_id, None)
        else:
            city.support[party_id] = value
        self._persist()
        return city

    def import_support(self, rows: dict[str, dict[str, dict[str, int]]]) -> int:
        """Заносит разобранную таблицу поддержки для сельских пунктов.

        Пункт, которого в округе ещё нет, создаётся; существующий узнаётся по
        названию, и его очки заменяются целиком — таблица считается полной
        картиной по этому пункту, а не добавкой к прежним очкам.

        Возвращает число обработанных пунктов. Если в той же таблице есть ещё
        и городские строки, лучше `import_support_table` — иначе ошибка в
        одной части может остаться незамеченной, пока применяется другая.
        """
        planned = self._plan_support(rows)
        self._apply_support(planned)
        self._persist()
        return len(planned)

    def import_city_support(self, rows: dict[str, dict[str, int]]) -> int:
        """Заносит разобранную таблицу поддержки для городов.

        Как и `import_support`: очки заменяются целиком, таблица считается
        полной картиной по городу, а не добавкой к прежним очкам.
        """
        planned = self._plan_city_support(rows)
        self._apply_city_support(planned)
        self._persist()
        return len(planned)

    def import_support_table(self, rows: dict[str, dict[str, dict[str, int]]],
                             city_rows: dict[str, dict[str, int]]) -> int:
        """Заносит обе части одной разобранной таблицы разом — сёла и города.

        Обе части сперва проверяются целиком, и только потом меняется хоть
        что-то: иначе ошибка в городской половине оставляла бы уже принятую
        сельскую половину в памяти, но не в файле — на экране одно, в
        проекте другое, а следующее сохранение тихо дописало бы её тоже.
        """
        planned = self._plan_support(rows)
        planned_cities = self._plan_city_support(city_rows)
        self._apply_support(planned)
        self._apply_city_support(planned_cities)
        self._persist()
        return len(planned) + len(planned_cities)

    def _plan_support(self, rows: dict[str, dict[str, dict[str, int]]],
                      ) -> list[tuple[District, str, dict[str, int]]]:
        """Проверяет таблицу сельских пунктов, ничего не меняя в проекте."""
        planned: list[tuple[District, str, dict[str, int]]] = []
        for district_id, settlements in (rows or {}).items():
            district = self._require_district(district_id)
            if district_seed.is_city(district.code):
                # Разбор таблицы сам не должен был сюда попасть — округ
                # города строкой распознаётся как город, а не как обычный
                # округ. Но файл могли и подправить руками.
                raise ValidationError(
                    f"«{district.name}» — городской округ, у него нет своих "
                    f"пунктов: очки идут через город «{district.region}».")
            for name, points in settlements.items():
                cleaned_name = self._clean_name(name)

                fresh: dict[str, int] = {}
                for party_id, value in points.items():
                    self._require_party(party_id)
                    fresh[party_id] = self._clean_support(value)

                total = sum(fresh.values())
                capacity = self.settlement_capacity(district, cleaned_name)
                if total > capacity:
                    raise ValidationError(
                        f"«{cleaned_name}»: роздано {total} очков, "
                        f"а в населённом пункте их {capacity}."
                    )
                planned.append((district, cleaned_name, fresh))
        return planned

    def _apply_support(self, planned: list[tuple[District, str, dict[str, int]]]) -> None:
        for district, name, fresh in planned:
            by_name = {s.name.strip().casefold(): s for s in district.settlements}
            settlement = by_name.get(name.casefold())
            if settlement is None:
                settlement = Settlement(id=new_id("s"), name=name)
                district.settlements.append(settlement)
            settlement.support = {p: n for p, n in fresh.items() if n > 0}

    def _plan_city_support(self, rows: dict[str, dict[str, int]],
                           ) -> list[tuple[Settlement, dict[str, int]]]:
        """Проверяет таблицу городов, ничего не меняя в проекте."""
        planned: list[tuple[Settlement, dict[str, int]]] = []
        for city_id, points in (rows or {}).items():
            city = self._require_city(city_id)

            fresh: dict[str, int] = {}
            for party_id, value in points.items():
                self._require_party(party_id)
                fresh[party_id] = self._clean_support(value)

            total = sum(fresh.values())
            if total > city.capacity:
                raise ValidationError(
                    f"«{city.name}»: роздано {total} очков, "
                    f"а в городе их {city.capacity}."
                )
            planned.append((city, fresh))
        return planned

    def _apply_city_support(self, planned: list[tuple[Settlement, dict[str, int]]]) -> None:
        for city, fresh in planned:
            city.support = {p: n for p, n in fresh.items() if n > 0}

    def settlement_capacity(self, district: District, name: str) -> int:
        """Запас очков пункта — по имени, ещё до того как он заведён.

        Нужен разбору таблицы: пункт из документа может в проекте не
        существовать, а проверить, не перебрали ли в нём очков, надо до
        записи.
        """
        for settlement in district.settlements:
            if _same_name(settlement.name, str(name)):
                return settlement.capacity
        return elections.SETTLEMENT_SUPPORT

    def support_modifier(self, district_id: str, party_id: str) -> float:
        """Модификатор поддержки партии в округе — очки, делённые на число НП.

        У городского округа очки не свои — общие на весь город (см.
        `Project.district_support`), а делитель всегда 1: несколько
        избирательных округов одного города получают один и тот же
        модификатор, а не дробят его ещё и на число районов.
        """
        district = self._require_district(district_id)
        return elections.support_modifier(
            self.project.district_support(district, party_id),
            self.project.district_settlement_count(district))

    # -- розыгрыш выборов ------------------------------------------------------

    def roll_election(self, convocation_id: str,
                      modifiers: dict[str, dict[str, dict]] | None = None,
                      national: dict[str, float] | None = None,
                      rng=None) -> Convocation:
        """Разыгрывает выборы по всем округам и пересобирает состав.

        :param modifiers: `{district_id: {party_id: {"modifier": число}}}` —
                          то, что ведущий выставил руками. Поддержка сюда не
                          передаётся: она считается из очков в населённых
                          пунктах.
        :param national: `{party_id: число}` — «настроение по стране»: тот
                         же свободный модификатор, но один на партию сразу
                         для всех округов, а не для одного конкретного.

        В розыгрыше участвуют все партии во всех округах — у каждой свой
        бросок 1–10, а поддержка и модификаторы просто прибавляются к нему.
        Партия без поддержки и без модификаторов идёт наравне с остальными,
        на одном голом броске: у неё просто нет прибавки.
        """
        conv = self._require_convocation(convocation_id)
        # Нечего разыгрывать не по нехватке модификаторов (их и не может не
        # хватать — участвуют все), а только если разыгрывать физически
        # некого или негде.
        if not self.project.districts:
            raise ValidationError("В проекте нет округов — разыгрывать нечего.")
        if not self.project.parties:
            raise ValidationError("В проекте нет партий — разыгрывать некого.")
        modifiers = modifiers or {}
        national = national or {}
        generator = rng or random.Random()

        # Ключи проверяем заранее: дальше идёт обход округов проекта, и
        # опечатка в имени округа или партии иначе просто потерялась бы —
        # выборы прошли бы «успешно», молча выбросив чужие модификаторы.
        for district_id, per_district in modifiers.items():
            self._require_district(district_id)
            for party_id in (per_district or {}):
                self._require_party(party_id)
        for party_id in national:
            self._require_party(party_id)

        rolls: dict[str, dict[str, elections.PartyRoll]] = {}
        for district in self.project.districts:
            per_district = modifiers.get(district.id, {})
            settlements = self.project.district_settlement_count(district)
            district_rolls: dict[str, elections.PartyRoll] = {}

            for party in self.project.parties:
                setup = per_district.get(party.id) or {}
                modifier = self._clean_bonus(setup.get("modifier", 0))
                mood = self._clean_bonus(national.get(party.id, 0))
                support = elections.support_modifier(
                    self.project.district_support(district, party.id), settlements)

                district_rolls[party.id] = elections.PartyRoll(
                    roll=elections.roll_dice(generator),
                    support=support, modifier=modifier, national=mood,
                )

            rolls[district.id] = district_rolls

        conv.rolls = rolls
        conv.votes = {district_id: elections.weights(per_party)
                      for district_id, per_party in rolls.items()}
        conv.votes = {d: v for d, v in conv.votes.items() if v}
        self._recount(conv)
        self._persist()
        return conv

    def district_shares(self, convocation_id: str, district_id: str) -> dict[str, float]:
        """Проценты голосов по округу — как их показывает разбор."""
        conv = self._require_convocation(convocation_id)
        return elections.shares(conv.rolls.get(district_id, {}))

    def _require_settlement(self, district_id: str, settlement_id: str) -> Settlement:
        district = self._require_district(district_id)
        settlement = district.settlement(settlement_id)
        if settlement is None:
            raise ValidationError("Населённый пункт не найден.")
        return settlement

    def _require_city(self, city_id: str) -> Settlement:
        city = self.project.city_by_id(city_id)
        if city is None:
            raise ValidationError("Город не найден.")
        return city

    def _clean_support(self, value: object) -> int:
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise ValidationError("Очки популярности должны быть целым числом.")
        try:
            points = int(value)
        except (TypeError, ValueError):
            raise ValidationError("Очки популярности должны быть целым числом.") from None
        if points < 0:
            raise ValidationError("Очки популярности не могут быть отрицательными.")
        return points

    def _clean_bonus(self, value: object) -> float:
        """Модификатор — любое конечное число, в том числе отрицательное."""
        if isinstance(value, bool):
            raise ValidationError("Модификатор должен быть числом.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValidationError("Модификатор должен быть числом.") from None
        # inf и nan проходят через float() молча, а дальше отравляют весь
        # расчёт: проценты округа становятся nan, места не раздаются.
        if number != number or number in (float("inf"), float("-inf")):
            raise ValidationError("Модификатор должен быть обычным числом.")
        return number

    def clear_election(self, convocation_id: str) -> Convocation:
        """Убирает результаты выборов созыва — состав снова набирается руками."""
        conv = self._require_convocation(convocation_id)
        conv.votes = {}
        conv.rolls = {}
        conv.seats = {}
        self._persist()
        return conv

    def district_allocation(self, convocation_id: str) -> dict[str, dict[str, int]]:
        """Места по округам: `{district_id: {party_id: места}}`."""
        conv = self._require_convocation(convocation_id)
        return elections.allocate_all(conv.votes, self.project.district_seats)

    def district_winners(self, convocation_id: str) -> dict[str, str]:
        """Победитель каждого округа — в его цвет карта красит округ."""
        conv = self._require_convocation(convocation_id)
        seats = self.project.district_seats
        winners = {}
        for district_id, votes in conv.votes.items():
            winner = elections.district_winner(votes, seats.get(district_id, 0))
            if winner:
                winners[district_id] = winner
        return winners

    def _recount(self, conv: Convocation) -> None:
        """Пересобирает состав созыва из голосов по округам."""
        allocation = elections.allocate_all(conv.votes, self.project.district_seats)
        conv.seats = elections.totals_by_party(allocation)

    def _require_district(self, district_id: str) -> District:
        district = self.project.district(district_id)
        if district is None:
            raise ValidationError("Округ не найден.")
        return district

    # -- свои цвета -----------------------------------------------------------

    def remember_recent_color(self, color: str) -> None:
        """Запоминает вручную подобранный цвет — свежий слева, без повторов;
        хранится в проекте, поэтому переживает перезапуск приложения."""
        color = self._clean_color(color)
        colors = self.project.recent_colors
        self.project.recent_colors = (
            [color] + [c for c in colors if c != color]
        )[:RECENT_COLORS_LIMIT]
        self._persist()

    # -- состояние для интерфейса -------------------------------------------

    @property
    def is_default_project(self) -> bool:
        """Проект лежит в папке приложения, а не в файле, выбранном вручную."""
        return self.path == self.default_path

    # -- проверки -----------------------------------------------------------

    def _require_party(self, party_id: str) -> Party:
        party = self.project.party(party_id)
        if party is None:
            raise ValidationError("Партия не найдена — возможно, она уже удалена.")
        return party

    def _require_convocation(self, convocation_id: str) -> Convocation:
        conv = self.project.convocation(convocation_id)
        if conv is None:
            raise ValidationError("Созыв не найден.")
        return conv

    def _clean_name(self, value: object) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValidationError("Название не может быть пустым.")
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationError(f"Название длиннее {MAX_NAME_LENGTH} символов.")
        return name

    def _clean_abbr(self, value: object) -> str:
        abbr = str(value or "").strip()
        if len(abbr) > MAX_ABBR_LENGTH:
            raise ValidationError(f"Сокращение длиннее {MAX_ABBR_LENGTH} символов.")
        return abbr

    def _clean_color(self, value: object) -> str:
        try:
            return normalize_color(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None

    def _clean_seat_count(self, value: object) -> int:
        # bool — подкласс int, а float молча обрезается до целого: и то и
        # другое отклоняем явно, чтобы «3,5 места» не превратились в 3.
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise ValidationError("Число мест должно быть целым числом.")
        try:
            count = int(value)
        except (TypeError, ValueError):
            raise ValidationError("Число мест должно быть целым числом.") from None
        if count < 0:
            raise ValidationError("Число мест не может быть отрицательным.")
        if count > self.project.total_seats:
            raise ValidationError(
                f"Одной партии нельзя дать больше {self.project.total_seats} мест."
            )
        return count
