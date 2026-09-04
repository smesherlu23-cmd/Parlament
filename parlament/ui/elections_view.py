"""Экран «Выборы»: модификаторы по округам и розыгрыш голосов.

Голоса не вводятся — они разыгрываются. Каждой партии в округе выпадает
1–10, к броску прибавляются модификаторы, и уже эти числа делятся между
партиями пропорционально.

Здесь выставляется то, что задаёт ведущий: свободный модификатор — любое
число, отрицательное действует как штраф. Причина не привязана к чему-то
одному вроде дебатов — это может быть что угодно по ходу партии. Второй
модификатор, поддержка, не вводится: он считается из очков в населённых
пунктах и показан справочно.

В розыгрыше участвуют только партии, у которых в округе есть хоть что-то —
поддержка или модификатор. Иначе каждая партия автоматически лезла бы в
каждый округ, включая те, где её нет.
"""

from __future__ import annotations

import flet as ft

from .. import district_seed
from ..elections import PartyRoll
from . import theme
from .mount import push

_BONUS_WIDTH = 58
_NAME_WIDTH = 200
_CELL_WIDTH = 104
_PREVIEW_WIDTH = 210


class ElectionsView:
    """Таблица «округ × партия» с модификаторами."""

    def __init__(self, app):
        self.app = app
        self.service = app.service
        #: `{district_id: {party_id: поле модификатора}}`.
        self.cells: dict[str, dict[str, ft.TextField]] = {}
        #: Подписи с ожидаемым раскладом справа от строки.
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
                        ft.Text("В клетке — модификатор, можно со знаком минус. "
                                "Поддержка берётся из населённых пунктов.",
                                size=theme.fs(12), color=theme.NEUTRAL_700),
                    ], spacing=3, tight=True, expand=True),
                    theme.ghost_button("Убрать все модификаторы",
                                       lambda _e: self._clear_all()),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
            ft.Container(
                expand=True,
                padding=ft.Padding.only(left=28, right=28, bottom=18),
                # Две прокрутки: вниз по округам и вбок — таблица с десятком
                # партий шире окна, и без этого правые столбцы недостижимы.
                content=ft.Row([
                    ft.Column([self._header(), ft.Divider(height=1, color=theme.DIVIDER),
                               *rows],
                              spacing=4, scroll=ft.ScrollMode.AUTO, expand=True),
                ], scroll=ft.ScrollMode.AUTO, expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START),
            ),
        ], spacing=0, expand=True)

    def _preload(self, conv) -> None:
        """Подставляет модификаторы прошлого розыгрыша, если он был.

        Экран выборов — не только ввод с нуля: результат нередко хочется
        перебросить, поменяв одну-две правки.
        """
        self.previous: dict[str, dict[str, PartyRoll]] = conv.rolls

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
            bonus.tooltip = "Модификатор, можно со знаком минус"

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

    def _on_bonus(self, event) -> None:
        """Оставляет в поле только число со знаком минус."""
        field = event.control
        raw = (field.value or "").replace(",", ".")
        cleaned = ""
        for index, ch in enumerate(raw):
            if ch.isdigit() or (ch == "-" and index == 0) or (ch == "." and "." not in cleaned):
                cleaned += ch
        if cleaned != field.value:
            field.value = cleaned
            push(field)
        district_id, _party_id = field.data
        self._refresh_preview(district_id)

    def _clear_all(self) -> None:
        """Стирает все выставленные модификаторы разом.

        Само поле поддержки не трогает: оно вообще не вводится здесь, а
        считается из очков в населённых пунктах.
        """
        for district_id, per_party in self.cells.items():
            for bonus in per_party.values():
                if bonus.value:
                    bonus.value = ""
                    push(bonus)
            self._refresh_preview(district_id)

    def _refresh_preview(self, district_id: str, live: bool = True) -> None:
        """Показывает поддержку партий и кто вообще идёт в округе.

        Именно поддержку, а не итог: бросок случаен, и обещать результат до
        розыгрыша было бы враньём.
        """
        district = self.service.project.district(district_id)
        preview = self.previews.get(district_id)
        if district is None or preview is None:
            return

        parts = []
        for party in self.app.parties:
            support = self.service.support_modifier(district_id, party.id)
            if not (support or self._running(district_id, party.id)):
                continue
            label = party.abbr or party.name
            parts.append(f"{label} {support:.1f}".replace(".", ","))

        preview.value = "  ".join(parts) if parts else "никто не идёт"
        preview.color = theme.TEXT if parts else theme.NEUTRAL_600
        if live:
            push(preview)

    def _running(self, district_id: str, party_id: str) -> bool:
        """Идёт ли партия в округе — по тому же правилу, что и `collect`.

        Считать «идёт» по непустому полю нельзя: вписанный ноль — это не
        модификатор, и в розыгрыш такая партия не попадёт. Предпросмотр
        обещал бы участие, которого не будет.
        """
        bonus = self.cells.get(district_id, {}).get(party_id)
        if bonus is None:
            return False
        return bool(_to_number(bonus.value))

    # -- сбор данных --------------------------------------------------------

    def collect(self) -> dict[str, dict[str, dict]]:
        """Модификаторы в виде, который принимает `roll_election`."""
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


def _to_number(text: str | None) -> float:
    try:
        return float((text or "").replace(",", "."))
    except ValueError:
        return 0.0
