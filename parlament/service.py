"""Операции над проектом — единственное место, где меняются данные.

Сервис отвечает за валидацию (раздел 8 ТЗ: нельзя отрицательное число мест,
нельзя превысить общее число мест) и за автосохранение: после каждой
успешной мутации проект пишется в текущий файл, поэтому закрытие окна
никогда не теряет работу.
"""

from __future__ import annotations

import random
from pathlib import Path

from . import coalitions as coalition_rules, district_seed, elections, store
from .model import (
    Coalition,
    Convocation,
    District,
    MIN_COALITION,
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
            # Блок, в котором осталась одна партия, распускается: договор
            # с самим собой — это не блок.
            for coalition in conv.coalitions:
                coalition.members = [pid for pid in coalition.members
                                     if pid != party.id]
            conv.coalitions = [c for c in conv.coalitions
                               if len(c.members) >= MIN_COALITION]
            if not conv.has_election:
                continue
            # Округ, из которого убрали партию, пересчитывается целиком: её
            # голоса расходятся между оставшимися, и доли снова дают сотню.
            # Без этого разбор показывал бы «16,9 % и все пять мест», а
            # барьер остался бы посчитанным по раскладу с ней.
            trimmed = {did: {pid: r for pid, r in per.items() if pid != party.id}
                       for did, per in conv.results.items()}
            conv.results = {did: elections.renormalize(per)
                            for did, per in trimmed.items() if per}
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
                     for per in conv.results.values() if party_id in per)
        return {
            "convocations": self.party_usage(party_id),
            "supportPoints": points,
            "settlements": settlements,
            "rolledDistricts": rolled,
            "recountedConvocations": sum(
                1 for conv in self.project.convocations
                if conv.has_election and any(party_id in per for per in conv.results.values())
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

    def district_points(self, district_id: str) -> dict[str, int]:
        """Очки поддержки партий в округе — `{party_id: очки}`.

        У городского округа очки общие на весь город (см.
        `Project.district_support`): несколько избирательных округов одной
        метрополии видят одну и ту же копилку, а не делят её ещё раз.

        Сумма, равная нулю, — это не «база у всех нулевая», а «очков здесь
        никто не раздавал»: доли тогда равные (см. `elections.base_shares`),
        и отличить одно от другого можно только по этим числам.
        """
        district = self._require_district(district_id)
        return {party.id: self.project.district_support(district, party.id)
                for party in self.project.parties}

    def district_capacity(self, district_id: str) -> int:
        """Сколько очков поддержки в округе можно раздать всего."""
        return self.project.district_capacity(self._require_district(district_id))

    def base_share(self, district_id: str, party_id: str) -> float:
        """База партии в округе — её доля голосов до поправок, в процентах.

        Считается от всего запаса очков, а не от розданных: неразобранные
        очки — неопределившиеся, и делятся поровну (см. `base_shares`).
        """
        return elections.base_shares(
            self.district_points(district_id),
            self.district_capacity(district_id),
        ).get(party_id, 0.0)

    # -- розыгрыш выборов ------------------------------------------------------

    def roll_election(self, convocation_id: str,
                      modifiers: dict[str, dict[str, dict]] | None = None,
                      national: dict[str, float] | None = None,
                      island: dict[str, dict[str, float]] | None = None,
                      rng=None) -> Convocation:
        """Разыгрывает выборы по всем округам и пересобирает состав.

        :param modifiers: `{district_id: {party_id: {"modifier": число}}}` —
                          местная поправка, которую ведущий выставил руками
                          на конкретный округ.
        :param national: `{party_id: число}` — «настроение по стране»: та же
                         поправка, но одна на партию сразу для всех округов.
        :param island: `{остров: {party_id: число}}` — то же самое, но на
                       один остров архипелага (см. `district_seed.islands`);
                       остров вычисляется по региону округа.

        У каждой партии в округе есть база — её доля от всего запаса очков
        округа, — к которой прибавляются (в процентных пунктах) настроение по
        стране, сдвиг по острову, местная поправка и случайное колебание;
        итог нормируется к 100 % на весь округ.

        Неразобранные очки — неопределившиеся: они делятся поровну между
        всеми, поэтому партия, не работавшая в округе, идёт не с нуля, а
        одно очко из шести даёт перевес, но не разгром (см. `base_shares`).
        """
        conv = self._require_convocation(convocation_id)
        # Нечего разыгрывать не по нехватке поправок (участвуют все и без
        # них), а только если разыгрывать физически некого или негде.
        if not self.project.districts:
            raise ValidationError("В проекте нет округов — разыгрывать нечего.")
        if not self.project.parties:
            raise ValidationError("В проекте нет партий — разыгрывать некого.")
        modifiers = modifiers or {}
        national = national or {}
        island = island or {}
        generator = rng or random.Random()

        # Ключи проверяем заранее: дальше идёт обход округов проекта, и
        # опечатка в имени округа, партии или острова иначе просто
        # потерялась бы — выборы прошли бы «успешно», молча выбросив чужую
        # поправку.
        for district_id, per_district in modifiers.items():
            self._require_district(district_id)
            for party_id in (per_district or {}):
                self._require_party(party_id)
        for party_id in national:
            self._require_party(party_id)
        known_islands = set(district_seed.islands())
        for island_name, per_party in island.items():
            if island_name not in known_islands:
                raise ValidationError(f"«{island_name}» — не остров архипелага.")
            for party_id in (per_party or {}):
                self._require_party(party_id)

        results: dict[str, dict[str, elections.PartyResult]] = {}
        for district in self.project.districts:
            per_district = modifiers.get(district.id, {})
            per_island = island.get(district_seed.island_of(district.region), {})
            points = {party.id: self.project.district_support(district, party.id)
                     for party in self.project.parties}
            base = elections.base_shares(points,
                                         self.project.district_capacity(district))

            raw: dict[str, float] = {}
            parts: dict[str, tuple[float, float, float, float]] = {}
            for party in self.project.parties:
                setup = per_district.get(party.id) or {}
                modifier = self._clean_bonus(setup.get("modifier", 0))
                mood = self._clean_bonus(national.get(party.id, 0))
                swing = self._clean_bonus(per_island.get(party.id, 0))
                wobble = elections.roll_wobble(generator)
                parts[party.id] = (mood, swing, modifier, wobble)
                raw[party.id] = base[party.id] + mood + swing + modifier + wobble

            share = elections.normalize_shares(raw)
            district_results: dict[str, elections.PartyResult] = {}
            for party in self.project.parties:
                mood, swing, modifier, wobble = parts[party.id]
                district_results[party.id] = elections.PartyResult(
                    base=base[party.id], national=mood, island=swing,
                    modifier=modifier, wobble=wobble, share=share[party.id],
                )
            results[district.id] = district_results

        conv.results = results
        self._recount(conv)
        self._persist()
        return conv

    def vote_shares(self, convocation_id: str) -> dict[str, float]:
        """Доля голосов партии по всей стране, в процентах: `{party_id: %}`.

        Округа взвешиваются по числу мандатов: округ на десять мест
        представляет больше людей, чем округ на два, и считать их наравне
        значило бы приравнять хутор к столице. Численности избирателей в
        игре нет, а мандаты — ближайшее, что её заменяет: их и раздавали по
        населению, когда рисовали карту.

        Считается по всем партиям округа, включая не прошедших барьер: это
        доля голосов, а не мест. Расхождение между этими двумя числами —
        как раз то, ради чего проценты голосов и показывают.

        Пустой словарь, если выборов не было.
        """
        conv = self._require_convocation(convocation_id)
        if not conv.results:
            return {}
        seats = self.project.district_seats

        weighted: dict[str, float] = {}
        total = 0.0
        for district_id, per_party in conv.results.items():
            weight = seats.get(district_id, 0)
            if weight <= 0:
                continue
            total += weight
            for party_id, result in per_party.items():
                weighted[party_id] = weighted.get(party_id, 0.0) + result.share * weight
        if total <= 0:
            return {}
        return {party_id: value / total for party_id, value in weighted.items()}

    def district_shares(self, convocation_id: str, district_id: str) -> dict[str, float]:
        """Проценты голосов по округу — как их показывает разбор.

        Включая тех, кто не прошёл проходной барьер: места им не достаются,
        но сам процент по-прежнему виден.
        """
        conv = self._require_convocation(convocation_id)
        return elections.shares(conv.results.get(district_id, {}))

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
        conv.results = {}
        conv.seats = {}
        self._persist()
        return conv

    @staticmethod
    def _weights(conv: Convocation) -> dict[str, dict[str, float]]:
        """Веса для дележа мест по округам — `{district_id: {party_id: доля}}`.

        Считаются из разбора, а не хранятся рядом с ним: это одни и те же
        числа, и вторая копия рано или поздно отстаёт от первой. Барьер —
        часть правила (см. `elections.weights`), поэтому партии ниже него
        сюда не попадают, хотя их доля в разборе видна.
        """
        weights = {district_id: elections.weights(per)
                   for district_id, per in conv.results.items()}
        return {district_id: w for district_id, w in weights.items() if w}

    def district_allocation(self, convocation_id: str) -> dict[str, dict[str, int]]:
        """Места по округам: `{district_id: {party_id: места}}`."""
        conv = self._require_convocation(convocation_id)
        return elections.allocate_all(self._weights(conv), self.project.district_seats)

    def district_winners(self, convocation_id: str) -> dict[str, str]:
        """Победитель каждого округа — в его цвет карта красит округ."""
        conv = self._require_convocation(convocation_id)
        seats = self.project.district_seats
        winners = {}
        for district_id, votes in self._weights(conv).items():
            winner = elections.district_winner(votes, seats.get(district_id, 0))
            if winner:
                winners[district_id] = winner
        return winners

    def _recount(self, conv: Convocation) -> None:
        """Пересобирает состав созыва из долей по округам."""
        allocation = elections.allocate_all(self._weights(conv),
                                            self.project.district_seats)
        conv.seats = elections.totals_by_party(allocation)

    def _require_district(self, district_id: str) -> District:
        district = self.project.district(district_id)
        if district is None:
            raise ValidationError("Округ не найден.")
        return district

    # -- коалиции -------------------------------------------------------------

    def create_coalition(self, convocation_id: str, name: str, color: str,
                         members: list[str]) -> Coalition:
        """Собирает блок из партий созыва."""
        conv = self._require_convocation(convocation_id)
        clean = self._clean_members(conv, members)
        coalition = Coalition(id=new_id("k"), name=self._clean_name(name),
                              color=self._clean_color(color), members=clean)
        conv.coalitions.append(coalition)
        self._persist()
        return coalition

    def update_coalition(self, convocation_id: str, coalition_id: str, name: str,
                         color: str, members: list[str]) -> Coalition:
        conv = self._require_convocation(convocation_id)
        coalition = self._require_coalition(conv, coalition_id)
        clean = self._clean_members(conv, members, besides=coalition_id)
        coalition.name = self._clean_name(name)
        coalition.color = self._clean_color(color)
        coalition.members = clean
        self._persist()
        return coalition

    def delete_coalition(self, convocation_id: str, coalition_id: str) -> None:
        """Распускает блок. Партии остаются на своих местах — блок был только
        договорённостью, а мандаты принадлежат им, а не ему."""
        conv = self._require_convocation(convocation_id)
        self._require_coalition(conv, coalition_id)
        conv.coalitions = [c for c in conv.coalitions if c.id != coalition_id]
        self._persist()

    def blocs(self, convocation_id: str) -> list:
        """Состав созыва блоками, крупнейший первым (см. `coalitions.blocs`)."""
        conv = self._require_convocation(convocation_id)
        return coalition_rules.blocs(self.project.parties, conv.seats, conv.coalitions)

    def _require_coalition(self, conv: Convocation, coalition_id: str) -> Coalition:
        found = next((c for c in conv.coalitions if c.id == coalition_id), None)
        if found is None:
            raise ValidationError("Коалиция не найдена — возможно, она уже распущена.")
        return found

    def _clean_members(self, conv: Convocation, members: list[str],
                       besides: str | None = None) -> list[str]:
        """Проверяет состав блока и убирает повторы, сохраняя порядок.

        Партия состоит не больше чем в одном блоке этого созыва: иначе её
        места вошли бы в оба, и сумма блоков перевалила бы за размер палаты —
        а «большинство» стало бы выдумкой.
        """
        clean = list(dict.fromkeys(str(pid) for pid in members or []))
        for party_id in clean:
            self._require_party(party_id)
        if len(clean) < MIN_COALITION:
            raise ValidationError(
                f"В коалиции должно быть хотя бы {MIN_COALITION} партии — "
                f"иначе это просто партия.")

        taken = {pid: c for c in conv.coalitions if c.id != besides
                 for pid in c.members}
        for party_id in clean:
            other = taken.get(party_id)
            if other is not None:
                party = self.project.party(party_id)
                raise ValidationError(
                    f"«{party.name}» уже состоит в коалиции «{other.name}». "
                    f"Партия может быть только в одном блоке.")
        return clean

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
