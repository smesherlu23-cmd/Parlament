"""Экран «Выборы»: базы, поправки и розыгрыш голосов.

Голоса в округе не вводятся — они считаются. У каждой партии есть база — её
доля от очков поддержки, розданных в округе игроками (столбец «Поддержка»
справа), — и к ней прибавляются (в процентных пунктах) три поправки:
свободный модификатор на конкретный округ, «настроение по стране» — тот же
модификатор, но один сразу на все округа партии, — и сдвиг по острову —
такой же, но на один остров архипелага, а не на всю карту и не на один
округ. Причина поправки не привязана к чему-то одному вроде дебатов — это
может быть что угодно по ходу партии. После всех поправок добавляется
небольшое случайное колебание, и доли округа нормируются к 100 %.

В розыгрыше участвуют все партии во всех округах — своя клетка не открывает
партии доступ в округ, а лишь прибавляет к её доле: без всякой базы и
поправок партия всё равно получает долю на одном колебании, просто без
форы. Партия ниже проходного барьера мест не получает, но её доля всё
равно видна в разборе округа — это не запрет участвовать.
"""

from __future__ import annotations

import flet as ft

from .. import district_seed
from ..elections import PartyResult, base_shares
from . import theme
from .mount import push

_BONUS_WIDTH = 58
_NAME_WIDTH = 200
_CELL_WIDTH = 104
_PREVIEW_WIDTH = 210


class ElectionsView:
    """Таблица «округ × партия» с базами и поправками."""

    def __init__(self, app):
        self.app = app
        self.service = app.service
        #: `{district_id: {party_id: поле местного модификатора}}`.
        self.cells: dict[str, dict[str, ft.TextField]] = {}
        #: `{party_id: поле «настроения по стране»}` — одна поправка сразу
        #: на все округа партии, не привязана ни к одному конкретному.
        self.national_cells: dict[str, ft.TextField] = {}
        #: `{остров: {party_id: поле сдвига по острову}}` — та же поправка,
        #: но на один остров архипелага, а не на всю карту.
        self.island_cells: dict[str, dict[str, ft.TextField]] = {}
        #: Подписи с ожидаемой базой справа от строки округа.
        self.previews: dict[str, ft.Text] = {}

    # -- сборка -------------------------------------------------------------

    def build(self) -> ft.Control:
        if not self.app.parties:
            return self._notice("Сначала создайте партии", "Новая партия",
                                self.app.show_parties)
        if not self.service.project.districts:
            return self._notice("В проекте нет округов", "К карте", self.app.show_map)

        conv = self.app.selected
        self._preload(conv)

        rows: list[ft.Control] = []
        last_island = None
        for district in self.service.project.districts:
            island = district_seed.island_of(district.region)
            if island != last_island:
                last_island = island
                rows.append(ft.Container(
                    padding=ft.Padding.only(top=14, bottom=4),
                    content=theme.label(island or "Прочие"),
                ))
                rows.append(self._island_row(island))
            rows.append(self._district_row(district))

        return ft.Column([
            ft.Container(
                padding=ft.Padding.only(left=28, right=28, top=18, bottom=6),
                content=ft.Row([
                    ft.Column([
                        ft.Text(f"{len(self.service.project.districts)} округов · "
                                f"{self.service.project.total_seats} мест · {conv.name}",
                                size=theme.fs(15), font_family=theme.FONT_SEMIBOLD,
                                color=theme.TEXT),
                        ft.Text("В клетке — поправка в процентных пунктах, можно со "
                                "знаком минус. Поддержка берётся из населённых пунктов; "
                                f"меньше {_threshold_label()} % голосов в округе — "
                                "без места, но доля всё равно видна в разборе.",
                                size=theme.fs(12), color=theme.NEUTRAL_700),
                    ], spacing=3, tight=True, expand=True),
                    theme.ghost_button("Убрать все поправки",
                                       lambda _e: self._clear_all()),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
            ft.Container(
                expand=True,
                padding=ft.Padding.only(left=28, right=28, bottom=18),
                # Две прокрутки: вниз по округам и вбок — таблица с десятком
                # партий шире окна, и без этого правые столбцы недостижимы.
                content=ft.Row([
                    ft.Column([self._header(), self._national_row(),
                               ft.Divider(height=1, color=theme.DIVIDER),
                               *rows],
                              spacing=4, scroll=ft.ScrollMode.AUTO, expand=True),
                ], scroll=ft.ScrollMode.AUTO, expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START),
            ),
        ], spacing=0, expand=True)

    def _preload(self, conv) -> None:
        """Подставляет поправки прошлых выборов, если они были.

        Экран выборов — не только ввод с нуля: результат нередко хочется
        пересчитать, поменяв одну-две правки.
        """
        self.previous: dict[str, dict[str, PartyResult]] = conv.results

        #: «Настроение по стране» одинаково для партии во всех округах —
        #: достаточно взять его из любого одного, чтобы подставить в поле.
        self.national_previous: dict[str, float] = {}
        first_district = next(iter(self.previous.values()), {})
        for party_id, result in first_district.items():
            if result.national:
                self.national_previous[party_id] = result.national

        #: Сдвиг по острову одинаков для партии во всех округах этого
        #: острова — берём его из первого сыгранного округа острова.
        self.island_previous: dict[str, dict[str, float]] = {}
        for district in self.service.project.districts:
            island = district_seed.island_of(district.region)
            if island in self.island_previous:
                continue
            per_party = self.previous.get(district.id)
            if not per_party:
                continue
            for party_id, result in per_party.items():
                if result.island:
                    self.island_previous.setdefault(island, {})[party_id] = result.island

    def _header(self) -> ft.Control:
        return ft.Row([
            ft.Container(theme.label("Округ"), width=_NAME_WIDTH),
            ft.Container(theme.label("Мест"), width=46),
            *[ft.Container(
                ft.Row([
                    theme.swatch(party.color, 10),
                    ft.Container(
                        ft.Text(party.abbr or party.name, size=theme.fs(11),
                                color=theme.NEUTRAL_600, no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        expand=True),
                ], spacing=5),
                width=_CELL_WIDTH, tooltip=party.name)
              for party in self.app.parties],
            ft.Container(theme.label("Поддержка"), width=_PREVIEW_WIDTH),
        ], spacing=10)

    def _national_row(self) -> ft.Control:
        """Строка «настроения по стране» — одна поправка партии сразу на
        все округа, а не на конкретный. Выглядит как строка округа, но
        вместо названия и мест — общая подпись."""
        cells: list[ft.Control] = [
            ft.Container(
                ft.Text("Настроение по стране", size=theme.fs(13),
                        font_family=theme.FONT_SEMIBOLD, color=theme.NEUTRAL_700,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip="Волна за или против партии сразу по всей карте — "
                                "прибавляется к её доле в каждом округе, вдобавок "
                                "к сдвигу по острову и местной поправке"),
                width=_NAME_WIDTH),
            ft.Container(width=46),
        ]
        for party in self.app.parties:
            value = self.national_previous.get(party.id, 0.0)
            field = theme.text_field(
                self._format_bonus(value) if value else "",
                width=_BONUS_WIDTH, text_align=ft.TextAlign.RIGHT)
            field.data = party.id
            field.on_change = self._on_national
            field.tooltip = "Поправка партии сразу на все округа, в п.п."
            self.national_cells[party.id] = field
            cells.append(ft.Container(field, width=_CELL_WIDTH))
        cells.append(ft.Container(width=_PREVIEW_WIDTH))

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=3),
            bgcolor=theme.NEUTRAL_100,
            content=ft.Row(cells, spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    def _island_row(self, island: str) -> ft.Control:
        """Строка сдвига по острову — та же идея, что и «настроение по
        стране», но применяется только к округам одного острова."""
        cells: list[ft.Control] = [
            ft.Container(
                ft.Text("Сдвиг по острову", size=theme.fs(12),
                        color=theme.NEUTRAL_600, no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=f"Поправка партии на все округа острова «{island}» "
                                "сразу — соседние округа острова обычно голосуют "
                                "похоже"),
                width=_NAME_WIDTH),
            ft.Container(width=46),
        ]
        per_party: dict[str, ft.TextField] = {}
        for party in self.app.parties:
            value = self.island_previous.get(island, {}).get(party.id, 0.0)
            field = theme.text_field(
                self._format_bonus(value) if value else "",
                width=_BONUS_WIDTH, text_align=ft.TextAlign.RIGHT)
            field.data = (island, party.id)
            field.on_change = self._on_island
            field.tooltip = "Поправка партии на округа этого острова, в п.п."
            per_party[party.id] = field
            cells.append(ft.Container(field, width=_CELL_WIDTH))
        cells.append(ft.Container(width=_PREVIEW_WIDTH))
        self.island_cells[island] = per_party

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=2),
            content=ft.Row(cells, spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    def _district_row(self, district) -> ft.Control:
        cells: list[ft.Control] = [
            ft.Container(
                ft.Text(district.name, size=theme.fs(14), color=theme.TEXT,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=district.name),
                width=_NAME_WIDTH),
            ft.Container(
                ft.Text(str(district.seats), size=theme.fs(14),
                        font_family=theme.FONT_SEMIBOLD, color=theme.NEUTRAL_700),
                width=46),
        ]

        per_party: dict[str, ft.TextField] = {}
        stored = self.previous.get(district.id, {})
        for party in self.app.parties:
            was = stored.get(party.id)
            bonus = theme.text_field(
                self._format_bonus(was.modifier) if was and was.modifier else "",
                width=_BONUS_WIDTH, text_align=ft.TextAlign.RIGHT)
            bonus.data = (district.id, party.id)
            bonus.on_change = self._on_bonus
            bonus.tooltip = "Местная поправка, в п.п., можно со знаком минус"

            per_party[party.id] = bonus
            cells.append(ft.Container(bonus, width=_CELL_WIDTH))

        preview = ft.Text(size=theme.fs(12), color=theme.NEUTRAL_700, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS)
        self.previews[district.id] = preview
        cells.append(ft.Container(preview, width=_PREVIEW_WIDTH))

        self.cells[district.id] = per_party
        self._refresh_preview(district.id, live=False)

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=3),
            content=ft.Row(cells, spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    # -- ввод ---------------------------------------------------------------

    @staticmethod
    def _sanitize(field: ft.TextField) -> None:
        """Оставляет в поле только число со знаком минус."""
        raw = (field.value or "").replace(",", ".")
        cleaned = ""
        for index, ch in enumerate(raw):
            if ch.isdigit() or (ch == "-" and index == 0) or (ch == "." and "." not in cleaned):
                cleaned += ch
        if cleaned != field.value:
            field.value = cleaned
            push(field)

    def _on_bonus(self, event) -> None:
        field = event.control
        self._sanitize(field)
        district_id, _party_id = field.data
        self._refresh_preview(district_id)

    def _on_national(self, event) -> None:
        self._sanitize(event.control)

    def _on_island(self, event) -> None:
        self._sanitize(event.control)

    def _clear_all(self) -> None:
        """Стирает все выставленные поправки разом — местные, по стране и
        по островам.

        Само поле поддержки не трогает: оно вообще не вводится здесь, а
        считается из очков в населённых пунктах.
        """
        for district_id, per_party in self.cells.items():
            for bonus in per_party.values():
                if bonus.value:
                    bonus.value = ""
                    push(bonus)
            self._refresh_preview(district_id)
        for field in self.national_cells.values():
            if field.value:
                field.value = ""
                push(field)
        for per_party in self.island_cells.values():
            for field in per_party.values():
                if field.value:
                    field.value = ""
                    push(field)

    def _refresh_preview(self, district_id: str, live: bool = True) -> None:
        """Показывает базу партий округа — их долю от розданных очков.

        Участвуют всегда все партии — этот столбец не про то, кто идёт
        (идут все), а про то, у кого есть организованная база. Именно базу,
        а не итог: колебание случайно, и обещать результат до розыгрыша
        было бы враньём.

        Округ, где очков не раздали вовсе, показывается словами, а не
        равными долями: «33 %» у каждого выглядит как настоящая поддержка,
        хотя на деле её нет ни у кого и решать будут поправки с колебанием.

        База считается от всего запаса округа: партия, разобравшая одно очко
        из шести, не должна выглядеть хозяйкой деревни (см. `base_shares`).
        Поэтому рядом показано и то, сколько очков вообще разобрано, — иначе
        непонятно, почему у всех есть проценты.
        """
        district = self.service.project.district(district_id)
        preview = self.previews.get(district_id)
        if district is None or preview is None:
            return

        points = self.service.district_points(district_id)
        capacity = self.service.district_capacity(district_id)
        if not any(points.values()):
            preview.value = "очков никто не раздал — доли равные"
            preview.color = theme.NEUTRAL_600
            if live:
                push(preview)
            return

        base = base_shares(points, capacity)
        parts = [f"{party.abbr or party.name} {base[party.id]:.0f} %".replace(".", ",")
                 for party in self.app.parties if base.get(party.id)]
        given = sum(points.values())
        if capacity and given < capacity:
            parts.append(f"· разобрано {given} из {capacity}")

        preview.value = "  ".join(parts)
        preview.color = theme.TEXT
        if live:
            push(preview)

    # -- сбор данных --------------------------------------------------------

    def collect(self) -> dict[str, dict[str, dict]]:
        """Местные поправки в виде, который принимает `roll_election`."""
        result: dict[str, dict[str, dict]] = {}
        for district_id, per_party in self.cells.items():
            district_setup: dict[str, dict] = {}
            for party_id, bonus in per_party.items():
                modifier = _to_number(bonus.value)
                if modifier:
                    district_setup[party_id] = {"modifier": modifier}
            if district_setup:
                result[district_id] = district_setup
        return result

    def collect_national(self) -> dict[str, float]:
        """«Настроение по стране» в виде, который принимает `roll_election`."""
        result: dict[str, float] = {}
        for party_id, field in self.national_cells.items():
            value = _to_number(field.value)
            if value:
                result[party_id] = value
        return result

    def collect_island(self) -> dict[str, dict[str, float]]:
        """Сдвиги по островам в виде, который принимает `roll_election`."""
        result: dict[str, dict[str, float]] = {}
        for island, per_party in self.island_cells.items():
            values = {party_id: _to_number(field.value)
                     for party_id, field in per_party.items()}
            values = {pid: v for pid, v in values.items() if v}
            if values:
                result[island] = values
        return result

    @staticmethod
    def _format_bonus(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else str(value)

    # -- пустые состояния ---------------------------------------------------

    def _notice(self, title: str, button: str, action) -> ft.Control:
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column([
                ft.Text(title, size=theme.fs(18), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT),
                theme.primary_button(button, lambda _e: action()),
            ], spacing=16,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )


def _threshold_label() -> str:
    from ..elections import THRESHOLD_PERCENT
    return str(int(THRESHOLD_PERCENT)) if THRESHOLD_PERCENT.is_integer() else str(THRESHOLD_PERCENT)


def _to_number(text: str | None) -> float:
    try:
        return float((text or "").replace(",", "."))
    except ValueError:
        return 0.0
