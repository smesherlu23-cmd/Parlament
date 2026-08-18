"""Операции над проектом — единственное место, где меняются данные.

Сервис отвечает за валидацию (раздел 8 ТЗ: нельзя отрицательное число мест,
нельзя превысить общее число мест) и за автосохранение: после каждой
успешной мутации проект пишется в текущий файл, поэтому закрытие окна
никогда не теряет работу.
"""

from __future__ import annotations

from pathlib import Path

from . import store
from .model import (
    Convocation,
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
