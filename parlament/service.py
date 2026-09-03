"""Операции над проектом — единственное место, где меняются данные.

Сервис отвечает за валидацию (раздел 8 ТЗ: нельзя отрицательное число мест,
нельзя превысить общее число мест) и за автосохранение: после каждой
успешной мутации проект пишется в текущий файл, поэтому закрытие окна
никогда не теряет работу.
"""

from __future__ import annotations

import random
from pathlib import Path

from . import elections, store
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
        """Удаляет партию из справочника; её места во всех созывах становятся
        нераспределёнными (на схеме — серыми)."""
        party = self._require_party(party_id)
        self.project.parties = [p for p in self.project.parties if p.id != party.id]
        for conv in self.project.convocations:
            conv.seats.pop(party.id, None)
        self._persist()

    def party_usage(self, party_id: str) -> list[dict]:
        """Где партия участвует — для предупреждения перед удалением."""
        self._require_party(party_id)
        return [
            {"convocationId": c.id, "convocationName": c.name, "seats": c.seats[party_id]}
            for c in self.project.convocations
            if c.seats.get(party_id, 0) > 0
        ]

    # -- распределение мест -------------------------------------------------

    def set_seats(self, convocation_id: str, party_id: str, seats: int) -> Convocation:
        """Ставит партии конкретное число мест в созыве."""
        conv = self._require_convocation(convocation_id)
        self._require_party(party_id)

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
        """Заводит НП в округе. В присланной карте их нет, поэтому список
        наполняет пользователь."""
        district = self._require_district(district_id)
        settlement = Settlement(id=new_id("s"), name=self._clean_name(name))
        district.settlements.append(settlement)
        self._persist()
        return settlement

    def rename_settlement(self, district_id: str, settlement_id: str,
                          name: str) -> Settlement:
        settlement = self._require_settlement(district_id, settlement_id)
        settlement.name = self._clean_name(name)
        self._persist()
        return settlement

    def delete_settlement(self, district_id: str, settlement_id: str) -> None:
        district = self._require_district(district_id)
        self._require_settlement(district_id, settlement_id)
        district.settlements = [s for s in district.settlements if s.id != settlement_id]
        self._persist()

    def set_support(self, district_id: str, settlement_id: str,
                    party_id: str, points: int) -> Settlement:
        """Ставит партии очки популярности в населённом пункте.

        Сумма по пункту ограничена: очков ровно столько, сколько несёт один
        НП, и раздать больше нельзя — иначе поддержка перестала бы быть
        дележом общего запаса.
        """
        settlement = self._require_settlement(district_id, settlement_id)
        self._require_party(party_id)

        value = self._clean_support(points)
        others = sum(n for pid, n in settlement.support.items() if pid != party_id)
        if others + value > elections.SETTLEMENT_SUPPORT:
            raise ValidationError(
                f"В населённом пункте всего {elections.SETTLEMENT_SUPPORT} очков "
                f"популярности. Другим партиям уже отдано {others}, "
                f"этой можно дать не больше {elections.SETTLEMENT_SUPPORT - others}."
            )

        if value == 0:
            settlement.support.pop(party_id, None)
        else:
            settlement.support[party_id] = value
        self._persist()
        return settlement

    def import_support(self, rows: dict[str, dict[str, dict[str, int]]]) -> int:
        """Заносит разобранную таблицу поддержки.

        Пункт, которого в округе ещё нет, создаётся; существующий узнаётся по
        названию, и его очки заменяются целиком — таблица считается полной
        картиной по этому пункту, а не добавкой к прежним очкам.

        Возвращает число обработанных пунктов.
        """
        touched = 0
        for district_id, settlements in (rows or {}).items():
            district = self._require_district(district_id)
            by_name = {s.name.strip().casefold(): s for s in district.settlements}

            for name, points in settlements.items():
                cleaned_name = self._clean_name(name)
                settlement = by_name.get(cleaned_name.casefold())
                if settlement is None:
                    settlement = Settlement(id=new_id("s"), name=cleaned_name)
                    district.settlements.append(settlement)
                    by_name[cleaned_name.casefold()] = settlement

                fresh: dict[str, int] = {}
                for party_id, value in points.items():
                    self._require_party(party_id)
                    fresh[party_id] = self._clean_support(value)

                total = sum(fresh.values())
                if total > elections.SETTLEMENT_SUPPORT:
                    raise ValidationError(
                        f"«{cleaned_name}»: роздано {total} очков, "
                        f"а в населённом пункте их {elections.SETTLEMENT_SUPPORT}."
                    )
                settlement.support = {p: n for p, n in fresh.items() if n > 0}
                touched += 1

        self._persist()
        return touched

    def support_modifier(self, district_id: str, party_id: str) -> float:
        """Модификатор поддержки партии в округе — очки, делённые на число НП."""
        district = self._require_district(district_id)
        return elections.support_modifier(district.support_points(party_id),
                                          len(district.settlements))

    # -- розыгрыш выборов ------------------------------------------------------

    def roll_election(self, convocation_id: str,
                      modifiers: dict[str, dict[str, dict]] | None = None,
                      rng=None) -> Convocation:
        """Разыгрывает выборы по всем округам и пересобирает состав.

        :param modifiers: `{district_id: {party_id: {"debate": число,
                          "agitation": bool}}}` — то, что ведущий выставил
                          руками. Поддержка сюда не передаётся: она считается
                          из очков в населённых пунктах.

        Участвуют только партии, у которых в округе есть хоть что-то — очки
        поддержки, бонус за дебаты или агитация. Иначе каждая партия
        автоматически лезла бы в каждый округ, включая те, где её нет.
        """
        conv = self._require_convocation(convocation_id)
        modifiers = modifiers or {}
        generator = rng or random.Random()

        # Ключи проверяем заранее: дальше идёт обход округов проекта, и
        # опечатка в имени округа или партии иначе просто потерялась бы —
        # выборы прошли бы «успешно», молча выбросив чужие модификаторы.
        for district_id, per_district in modifiers.items():
            self._require_district(district_id)
            for party_id in (per_district or {}):
                self._require_party(party_id)

        rolls: dict[str, dict[str, elections.PartyRoll]] = {}
        for district in self.project.districts:
            per_district = modifiers.get(district.id, {})
            settlements = len(district.settlements)
            district_rolls: dict[str, elections.PartyRoll] = {}

            for party in self.project.parties:
                setup = per_district.get(party.id) or {}
                debate = self._clean_bonus(setup.get("debate", 0))
                agitation = bool(setup.get("agitation"))
                support = elections.support_modifier(
                    district.support_points(party.id), settlements)

                if not (support or debate or agitation):
                    continue
                district_rolls[party.id] = elections.PartyRoll(
                    roll=elections.roll_dice(generator),
                    support=support, debate=debate, agitation=agitation,
                )

            if district_rolls:
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
        """Бонус за дебаты — любое число, в том числе отрицательное."""
        if isinstance(value, bool):
            raise ValidationError("Бонус за дебаты должен быть числом.")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValidationError("Бонус за дебаты должен быть числом.") from None

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
        """Победитель каждого округа — по нему карта красит маркеры."""
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
