"""Сборка приложения: состояние, экраны, действия.

Логика живёт в `service` и вызывается напрямую — здесь нет ни сети, ни
межпроцессного обмена: Flet-приложение и бизнес-логика работают в одном
процессе Python.

Части экрана, которые меняются от ввода (схема, легенда, сводка, список
созывов), обновляются точечно — перестраивать всё окно на каждое нажатие
клавиши было бы заметно медленнее: только на схеме 240 фигур.
"""

from __future__ import annotations

import flet as ft

from .. import model
from ..model import Convocation, Party
from ..service import ParlamentService, ValidationError
from ..store import StoreError
from . import dialogs, format as fmt, theme
from .mount import push
from .export import LegendEntry, render_png, suggest_file_name
from .elections_view import ElectionsView
from .map_chart import map_image_path
from .map_export import render_map_png
from .map_view import MapView
from .parties_view import PartiesView
from .seat_chart import SeatChart, chart_height_for_width
from .support_view import SupportView
from .support_file import export_support_template, read_support_file


class ParlamentApp:
    """Окно приложения: шапка, три колонки, диалоги."""

    def __init__(self, page: ft.Page, service: ParlamentService):
        self.page = page
        self.service = service

        self.view = "parliament"   # parliament | parties | map | elections | support
        #: Заполняются при сборке соответствующего экрана.
        self.map_chart = None
        self.elections = None
        #: Какие округа раскрыты на экране поддержки — чтобы список не
        #: схлопывался после каждой правки очков.
        self.support_opened: set[str] = set()
        self.selected_convocation_id: str | None = None
        self.editing_archived: str | None = None

        self.file_picker = ft.FilePicker()
        self.body = ft.Container(expand=True)
        self.appbar_slot = ft.Container()

    # -- сборка -------------------------------------------------------------

    def build(self) -> None:
        page = self.page
        page.title = "Парламент"
        page.bgcolor = theme.BG
        page.padding = 0
        page.spacing = 0
        page.fonts = theme.FONTS
        page.theme = theme.build_theme()
        page.theme_mode = ft.ThemeMode.LIGHT
        page.services.append(self.file_picker)

        page.window.width = theme.WINDOW_WIDTH
        page.window.height = theme.WINDOW_HEIGHT
        page.window.min_width = theme.WINDOW_MIN_WIDTH
        page.window.min_height = theme.WINDOW_MIN_HEIGHT

        self.selected_convocation_id = self.service.project.active_convocation.id
        page.add(ft.Column([self.appbar_slot, self.body], spacing=0, expand=True))
        self.render()

    # -- выборки ------------------------------------------------------------

    @property
    def total_seats(self) -> int:
        return self.service.project.total_seats

    @property
    def majority_seats(self) -> int:
        """Мест для большинства: половина плюс одно."""
        return self.total_seats // 2 + 1

    @property
    def parties(self) -> list[Party]:
        return self.service.project.parties

    @property
    def recent_colors(self) -> list[str]:
        """Свои цвета, недавно подобранные вручную (свежий слева) — хранятся
        в проекте, поэтому переживают перезапуск приложения."""
        return self.service.project.recent_colors

    @property
    def convocations(self) -> list[Convocation]:
        """Новейшие сверху — как в макете."""
        return list(reversed(self.service.project.convocations))

    @property
    def selected(self) -> Convocation:
        conv = self.service.project.convocation(self.selected_convocation_id)
        return conv or self.service.project.active_convocation

    @property
    def is_editable(self) -> bool:
        """Открытый созыв правится всегда; архивный — после «Править состав»."""
        conv = self.selected
        return not conv.is_fixed or self.editing_archived == conv.id

    @property
    def manual_seats(self) -> bool:
        """Набираются ли места руками.

        После выборов — нет: места посчитаны по округам, и правка их полем
        ввода развела бы зал с картой, где округа уже покрашены. Чтобы вернуть
        ручной набор, выборы сбрасываются целиком.
        """
        return self.is_editable and not self.selected.has_election

    def distribution(self, conv: Convocation) -> list[tuple[Party, int]]:
        """Партии с местами по убыванию — крупнейшая фракция уходит влево."""
        pairs = [(p, conv.seats.get(p.id, 0)) for p in self.parties]
        return sorted([pair for pair in pairs if pair[1] > 0],
                      key=lambda pair: pair[1], reverse=True)

    def bar_segments(self, conv: Convocation) -> list[tuple[str, int]]:
        segments = [(party.color, seats) for party, seats in self.distribution(conv)]
        remaining = self.total_seats - sum(seats for _p, seats in self.distribution(conv))
        if remaining > 0:
            segments.append((theme.EMPTY_SEAT, remaining))
        return segments

    def used_seats(self, conv: Convocation) -> int:
        return sum(conv.seats.values())

    # -- отрисовка ----------------------------------------------------------

    def render(self) -> None:
        """Полная пересборка экрана — при смене вида, созыва или состава партий."""
        self.appbar_slot.content = self._build_appbar()
        if self.view == "parties":
            self.body.content = PartiesView(self).build()
        elif self.view == "map":
            self.body.content = MapView(self).build()
        elif self.view == "support":
            self.body.content = SupportView(self).build()
        elif self.view == "elections":
            # Экран выборов держит поля ввода, поэтому живёт в поле: с него
            # потом собираются введённые голоса.
            self.elections = ElectionsView(self)
            self.body.content = self.elections.build()
        else:
            self.body.content = self._build_parliament()
        self.page.update()

    def _build_appbar(self) -> ft.Control:
        if self.view == "parties":
            left: list[ft.Control] = [
                theme.ghost_button("← К парламенту", lambda _e: self.show_parliament()),
                ft.Text("ПАРТИИ", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            right: list[ft.Control] = [
                theme.primary_button("Новая партия", lambda _e: self.new_party()),
            ]
        elif self.view == "map":
            left = [
                theme.ghost_button("← К парламенту", lambda _e: self.show_parliament()),
                ft.Text("КАРТА", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            has_districts = bool(self.service.project.districts)
            # Разыграть выборы в архивном созыве — значит переписать историю,
            # а на это в программе есть отдельный шаг «Править состав».
            votable = has_districts and bool(self.parties) and self.is_editable
            if not self.parties:
                why = "Сначала создайте партии"
            elif not self.is_editable:
                why = "Созыв в истории — сначала «Править состав»"
            else:
                why = None
            right = [
                theme.secondary_button(
                    "Экспорт карты в PNG", lambda _e: self.export_map_png(),
                    disabled=not (has_districts and self.selected.has_election),
                ),
                theme.secondary_button(
                    "Поддержка", lambda _e: self.show_support(),
                    disabled=not (has_districts and self.parties),
                ),
                theme.primary_button(
                    "Выборы", lambda _e: self.show_elections(),
                    disabled=not votable, tooltip=why,
                ),
            ]
        elif self.view == "support":
            left = [
                theme.ghost_button("← К карте", lambda _e: self.show_map()),
                ft.Text("ПОДДЕРЖКА", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            usable = bool(self.parties and self.service.project.districts)
            right = [
                theme.ghost_button("Шаблон таблицы",
                                   lambda _e: self.save_support_template(),
                                   disabled=not usable),
                theme.secondary_button("Загрузить таблицу",
                                       lambda _e: self.load_support_file(),
                                       disabled=not usable),
            ]
        elif self.view == "elections":
            left = [
                theme.ghost_button("← К карте", lambda _e: self.show_map()),
                ft.Text("ВЫБОРЫ", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            usable = bool(self.parties and self.service.project.districts)
            right = [
                theme.primary_button("Провести выборы", lambda _e: self.apply_election(),
                                     disabled=not usable),
            ]
        else:
            archive_view = self.selected.is_fixed and not self.is_editable

            left = [
                ft.Text("ПАРЛАМЕНТ", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            if archive_view:
                left.append(ft.Container(
                    bgcolor=theme.NEUTRAL_100,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                    border_radius=1,
                    content=ft.Text("Просмотр истории", size=theme.fs(11), color=theme.NEUTRAL_900),
                ))

            right = [
                theme.secondary_button("Партии", lambda _e: self.show_parties()),
                # Показывается всегда, даже когда округов в проекте ещё нет:
                # именно с этого экрана они и заводятся. Спрятанная кнопка
                # означала бы, что в проекте, начатом до карты, до выборов не
                # добраться вовсе.
                theme.secondary_button("Карта", lambda _e: self.show_map()),
                theme.secondary_button("Поддержка", lambda _e: self.show_support()),
            ]
            if archive_view:
                right.append(theme.primary_button("Править состав", lambda _e: self.edit_archived()))

        return ft.Container(
            bgcolor=theme.BG,
            padding=ft.Padding.symmetric(horizontal=20, vertical=11),
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Row(
                [*left, ft.Container(expand=True), *right],
                spacing=18,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # -- главный экран ------------------------------------------------------

    def _build_parliament(self) -> ft.Control:
        conv = self.selected
        has_parties = bool(self.parties)

        self.chart = SeatChart(self.total_seats, self.service.project.rows,
                               [(p.color, s) for p, s in self.distribution(conv)])
        self.legend_row = ft.Row(wrap=True, spacing=26, run_spacing=8)
        self.majority_text = ft.Text(size=theme.fs(12))
        self.conv_list = ft.Column(spacing=6, tight=True)
        self.stage_meta = ft.Text(size=theme.fs(13), color=theme.NEUTRAL_700)

        center = self._build_stage(conv) if has_parties else self._build_empty_stage()
        right = self._build_seat_rail(conv) if has_parties else self._build_empty_rail()

        self._refresh_conv_list(live=False)
        if has_parties:
            self._refresh_derived(live=False)

        return ft.Row(
            [self._build_conv_rail(), center, right],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _build_conv_rail(self) -> ft.Control:
        return ft.Container(
            width=theme.RAIL_LEFT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=16, vertical=18),
            border=ft.Border.only(right=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Column([
                theme.label("Созывы"),
                self.conv_list,
                theme.ghost_button(
                    "+ Новый созыв", lambda _e: self.new_convocation(),
                    disabled=not self.parties,
                ),
            ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True),
        )

    def _refresh_conv_list(self, live: bool = True) -> None:
        many = len(self.service.project.convocations) > 1
        cards = []
        for conv in self.convocations:
            selected = conv.id == self.selected.id
            used = self.used_seats(conv)
            note = "Редактируется" if not conv.is_fixed else f"{used} из {self.total_seats} мест"

            header = [ft.Container(
                ft.Text(conv.name, size=theme.fs(14), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, no_wrap=True),
                expand=True,
            )]
            # Единственный созыв удалить нельзя — история не может опустеть,
            # так что кнопке тогда просто нечего делать.
            if many:
                header.append(theme.icon_button(
                    ft.Icons.CLOSE, lambda _e, c=conv: self.delete_convocation(c),
                    danger=True, tooltip="Удалить созыв", size=13,
                ))

            cards.append(ft.Container(
                bgcolor=theme.ACCENT_100 if selected else ft.Colors.TRANSPARENT,
                padding=ft.Padding.symmetric(horizontal=10, vertical=9),
                border_radius=theme.RADIUS,
                animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
                data=conv.id,
                on_click=lambda e: self.select_convocation(e.control.data),
                ink=True,
                content=ft.Column([
                    ft.Row(header, spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    dialogs.seat_bar(self.bar_segments(conv)),
                    ft.Text(note, size=theme.fs(11),
                            color=theme.ACCENT_700 if not conv.is_fixed else theme.NEUTRAL_600),
                ], spacing=6, tight=True),
            ))
        self.conv_list.controls = cards
        if live:
            push(self.conv_list)

    def _build_stage(self, conv: Convocation) -> ft.Control:
        head: list[ft.Control] = [theme.heading(conv.name)]
        if self.is_editable:
            head.append(theme.ghost_button("Переименовать", lambda _e: self.rename_convocation()))
        head.extend([ft.Container(expand=True), self.stage_meta])

        return ft.Container(
            expand=True,
            padding=ft.Padding.only(left=28, right=28, top=22, bottom=18),
            content=ft.Column([
                ft.Row(head, spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
                ft.Container(self.chart, padding=ft.Padding.only(top=14)),
                ft.Container(self.legend_row, padding=ft.Padding.only(top=8)),
                ft.Container(expand=True),           # свободное место до заметки внизу
                ft.Container(
                    padding=ft.Padding.only(top=14),
                    content=ft.Row([
                        ft.Text(f"Большинство — {fmt.pluralize(self.majority_seats, fmt.SEATS)}.",
                                size=theme.fs(12), color=theme.NEUTRAL_600),
                        self.majority_text,
                    ], spacing=10),
                ),
            ], spacing=0, expand=True),
        )

    def _build_empty_stage(self) -> ft.Control:
        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=28, vertical=26),
            content=ft.Column([
                ft.Container(
                    SeatChart(self.total_seats, self.service.project.rows, [],
                              opacity=0.5, height=chart_height_for_width(560)),
                    width=560,
                ),
                ft.Text("Партий пока нет", size=theme.fs(22), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT),
                theme.primary_button("Создать первую партию", lambda _e: self.show_parties()),
            ],
                spacing=16,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_seat_rail(self, conv: Convocation) -> ft.Control:
        if not self.is_editable:
            return self._build_readonly_rail(conv)
        if conv.has_election:
            return self._build_election_rail(conv)

        self.seat_fields: dict[str, ft.TextField] = {}
        rows: list[ft.Control] = []
        for party in self.parties:
            field = theme.text_field(
                str(conv.seats.get(party.id, 0)),
                width=72,
                text_align=ft.TextAlign.RIGHT,
                keyboard_numeric=True,
            )
            field.data = party.id
            field.on_change = self._on_seat_change
            field.on_blur = self._on_seat_blur
            self.seat_fields[party.id] = field

            rows.append(ft.Row([
                theme.swatch(party.color, 12),
                ft.Container(
                    ft.Text(party.name, size=theme.fs(14), color=theme.TEXT, no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS, tooltip=party.name),
                    expand=True,
                ),
                field,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        self.used_text = ft.Text(size=theme.fs(16), font_family=theme.FONT_SEMIBOLD, color=theme.TEXT)
        self.remaining_text = ft.Text(size=theme.fs(16), font_family=theme.FONT_SEMIBOLD)
        self.progress = ft.ProgressBar(bgcolor=theme.NEUTRAL_300, color=theme.ACCENT,
                                       height=6, border_radius=0, value=0)
        self.remaining_note = ft.Text(size=theme.fs(12))
        self.reset_button = theme.secondary_button(
            "Сбросить распределение", lambda _e: self.reset_seats(), expand=True)

        return ft.Container(
            width=theme.RAIL_RIGHT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            border=ft.Border.only(left=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Column([
                theme.label("Распределение мест"),
                ft.Container(
                    ft.Column(rows, spacing=2, scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    padding=ft.Padding.only(top=12),
                ),
                ft.Container(
                    padding=ft.Padding.only(top=14),
                    border=ft.Border.only(top=ft.BorderSide(1, theme.DIVIDER)),
                    content=ft.Column([
                        ft.Row([ft.Text("Распределено", size=theme.fs(13), color=theme.NEUTRAL_700),
                                ft.Container(expand=True), self.used_text],
                               vertical_alignment=ft.CrossAxisAlignment.END),
                        ft.Row([ft.Text("Остаток", size=theme.fs(13), color=theme.NEUTRAL_700),
                                ft.Container(expand=True), self.remaining_text],
                               vertical_alignment=ft.CrossAxisAlignment.END),
                        self.progress,
                        self.remaining_note,
                    ], spacing=7, tight=True),
                ),
                ft.Container(
                    padding=ft.Padding.only(top=18),
                    content=ft.Column([
                        ft.Row([self.reset_button]),
                        ft.Row([theme.primary_button("Экспортировать в PNG",
                                                     lambda _e: self.export_png(), expand=True)]),
                    ], spacing=8, tight=True),
                ),
            ], spacing=0, expand=True),
        )

    def _build_election_rail(self, conv: Convocation) -> ft.Control:
        """Состав, посчитанный выборами: список без полей ввода.

        Места здесь производные — их источник округа, а не человек. Дать
        поправить их полем ввода значило бы развести зал с картой и с разбором
        округа, где те же места уже расписаны по партиям. Вернуть ручной набор
        можно, сбросив выборы.
        """
        rows = [
            ft.Container(
                padding=ft.Padding.symmetric(vertical=7),
                border=ft.Border.only(bottom=ft.BorderSide(1, "#14201e1d")),
                content=ft.Row([
                    theme.swatch(party.color, 12),
                    ft.Container(
                        ft.Text(party.name, size=theme.fs(14), color=theme.TEXT,
                                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                                tooltip=party.name),
                        expand=True),
                    ft.Text(str(seats), size=theme.fs(15),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                ], spacing=10),
            )
            for party, seats in self.distribution(conv)
        ]
        districts = len(conv.rolls)

        return ft.Container(
            width=theme.RAIL_RIGHT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            border=ft.Border.only(left=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Column([
                theme.label("Состав по итогам выборов"),
                ft.Container(
                    padding=ft.Padding.only(top=6),
                    content=ft.Text(
                        f"Места посчитаны по {fmt.pluralize(districts, fmt.DISTRICTS)}. "
                        "Чтобы набрать состав руками, сбросьте выборы.",
                        size=theme.fs(12), color=theme.NEUTRAL_700),
                ),
                ft.Container(
                    ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    padding=ft.Padding.only(top=10),
                ),
                ft.Container(
                    padding=ft.Padding.only(top=18),
                    content=ft.Column([
                        ft.Row([theme.secondary_button("Сбросить выборы",
                                                       lambda _e: self.reset_election(),
                                                       expand=True)]),
                        ft.Row([theme.primary_button("Экспортировать в PNG",
                                                     lambda _e: self.export_png(),
                                                     expand=True)]),
                    ], spacing=8, tight=True),
                ),
            ], spacing=0, expand=True),
        )

    def _build_readonly_rail(self, conv: Convocation) -> ft.Control:
        rows = [
            ft.Container(
                padding=ft.Padding.symmetric(vertical=7),
                border=ft.Border.only(bottom=ft.BorderSide(1, "#14201e1d")),
                content=ft.Row([
                    theme.swatch(party.color, 12),
                    ft.Container(ft.Text(party.name, size=theme.fs(14), color=theme.TEXT, no_wrap=True),
                                 expand=True),
                    ft.Text(str(seats), size=theme.fs(15), font_family=theme.FONT_SEMIBOLD,
                            color=theme.TEXT),
                ], spacing=10),
            )
            for party, seats in self.distribution(conv)
        ]
        return ft.Container(
            width=theme.RAIL_RIGHT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            border=ft.Border.only(left=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Column([
                theme.label("Состав — только чтение"),
                ft.Container(
                    ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    padding=ft.Padding.only(top=12),
                ),
                ft.Container(
                    padding=ft.Padding.only(top=18),
                    content=ft.Row([theme.secondary_button(
                        "Экспортировать в PNG", lambda _e: self.export_png(), expand=True)]),
                ),
            ], spacing=0, expand=True),
        )

    def _build_empty_rail(self) -> ft.Control:
        return ft.Container(
            width=theme.RAIL_RIGHT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            border=ft.Border.only(left=ft.BorderSide(1, theme.DIVIDER)),
            content=theme.label("Распределение мест"),
        )

    # -- точечное обновление ------------------------------------------------

    def _refresh_derived(self, live: bool = True) -> None:
        """Пересчитывает всё, что зависит от распределения мест.

        :param live: при сборке экрана — False: контролы ещё не на странице,
                     и отправлять их рано; страница обновится целиком в `render`.
        """
        conv = self.selected
        distribution = self.distribution(conv)
        used = self.used_seats(conv)
        remaining = self.total_seats - used

        self.chart.set_data(self.total_seats, self.service.project.rows,
                            [(p.color, s) for p, s in distribution])

        if distribution:
            self.legend_row.controls = [
                ft.Row([
                    theme.swatch(party.color, 11),
                    ft.Text(party.name, size=theme.fs(13), color=theme.TEXT),
                    ft.Text(str(seats), size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                            color=theme.TEXT),
                    ft.Text(fmt.percent(seats, self.total_seats), size=theme.fs(13),
                            color=theme.NEUTRAL_600),
                ], spacing=8, tight=True)
                for party, seats in distribution
            ]
        else:
            self.legend_row.controls = []

        largest = distribution[0] if distribution else None
        if largest and largest[1] >= self.majority_seats:
            self.majority_text.value = f"{largest[0].name} — абсолютное большинство"
            self.majority_text.color = theme.ACCENT_2_700
        elif largest:
            self.majority_text.value = (f"Крупнейшая фракция: {largest[0].name} "
                                        f"({largest[1]}), большинства нет")
            self.majority_text.color = theme.NEUTRAL_700
        else:
            self.majority_text.value = "Мест никому не отдано."
            self.majority_text.color = theme.NEUTRAL_700

        if not self.manual_seats and self.selected.has_election:
            self.stage_meta.value = (
                f"Выборы: {fmt.pluralize(len(self.selected.rolls), fmt.DISTRICTS)} "
                f"· {used} из {self.total_seats} мест")
        if self.manual_seats:
            self.stage_meta.value = f"Распределено {used} из {self.total_seats}"
            self.used_text.value = f"{used} / {self.total_seats}"
            self.remaining_text.value = str(remaining)
            self.remaining_text.color = theme.NEUTRAL_700 if remaining == 0 else theme.ACCENT_2_700
            self.progress.value = used / self.total_seats
            self.remaining_note.value = (
                "Все места распределены." if remaining == 0 else
                f"Осталось распределить {fmt.pluralize(remaining, fmt.SEATS)}."
            )
            self.remaining_note.color = (theme.NEUTRAL_700 if remaining == 0
                                         else theme.ACCENT_2_700)
            self.reset_button.disabled = used == 0

        self._refresh_conv_list(live=live)
        if live:
            for control in (self.chart, self.legend_row, self.majority_text,
                            self.stage_meta):
                push(control)
            if self.manual_seats:
                for control in (self.used_text, self.remaining_text, self.progress,
                                self.remaining_note, self.reset_button):
                    push(control)

    # -- правка мест --------------------------------------------------------

    def _on_seat_change(self, event: ft.ControlEvent) -> None:
        field = event.control
        party_id = field.data
        conv = self.selected

        raw = (field.value or "").strip()
        if raw == "":
            return                      # пустое поле — пользователь ещё печатает

        try:
            requested = int(raw)
        except ValueError:
            # Возвращаем прежнее значение: буквам в поле мест делать нечего.
            field.value = str(conv.seats.get(party_id, 0))
            push(field)
            return

        # Тот же потолок, что и в service: правку не отклоняем, а подрезаем —
        # так поле ведёт себя предсказуемо.
        others = sum(n for pid, n in conv.seats.items() if pid != party_id)
        value = max(0, min(requested, self.total_seats - others))

        try:
            self.service.set_seats(conv.id, party_id, value)
        except (ValidationError, StoreError) as error:
            self.toast(str(error), error=True)
            return

        if value != requested:
            field.value = str(value)
            push(field)
        self._refresh_derived()

    def _on_seat_blur(self, event: ft.ControlEvent) -> None:
        """Пустое поле после ухода фокуса — это ноль, а не «не задано»."""
        field = event.control
        if (field.value or "").strip() == "":
            field.value = "0"
            push(field)
            try:
                self.service.set_seats(self.selected.id, field.data, 0)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self._refresh_derived()

    # -- навигация ----------------------------------------------------------

    def show_parliament(self) -> None:
        self.view = "parliament"
        self.render()

    def show_parties(self) -> None:
        self.view = "parties"
        self.render()

    def show_map(self) -> None:
        self.view = "map"
        self.render()

    def show_elections(self) -> None:
        self.view = "elections"
        self.render()

    def show_support(self) -> None:
        self.view = "support"
        self.render()

    def adopt_map_districts(self) -> None:
        """Заводит округа карты в проекте, начатом до её появления."""
        old_total = self.total_seats

        def confirm(_event) -> None:
            try:
                self.service.adopt_map_districts()
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()
            self.toast(f"Округа добавлены: {len(self.service.project.districts)} шт., "
                       f"{self.total_seats} мест.")

        self.page.show_dialog(dialogs.adopt_districts_dialog(
            old_total, model.default_districts(), confirm, lambda _e: self.close_dialog()))

    # -- выборы -------------------------------------------------------------

    def apply_election(self) -> None:
        """Разыгрывает выборы и уводит на раскрашенную карту.

        Созыв из истории не переигрывается: для этого есть «Править состав».
        """
        if not self.is_editable:
            # Тем же розыгрышем можно было бы переписать историю в обход
            # «Править состав» — единственного места, где это решается.
            self.toast("Созыв лежит в истории. Чтобы переиграть выборы, "
                       "откройте его кнопкой «Править состав».", error=True)
            return
        modifiers = self.elections.collect()
        national = self.elections.collect_national()
        try:
            self.service.roll_election(self.selected.id, modifiers, national=national)
        except (ValidationError, StoreError) as error:
            self.toast(str(error), error=True)
            return

        conv = self.selected
        filled = len(conv.rolls)
        total = len(self.service.project.districts)
        self.show_map()
        self.toast(f"Выборы разыграны: {fmt.pluralize(filled, fmt.DISTRICTS)} из {total}.")

    def show_district(self, district_id: str) -> None:
        """Расклад одного округа — по клику на него на карте."""
        district = self.service.project.district(district_id)
        if district is None:
            return
        conv = self.selected
        allocation = self.service.district_allocation(conv.id).get(district_id, {})
        rolls = conv.rolls.get(district_id, {})
        shares = self.service.district_shares(conv.id, district_id)
        by_id = {p.id: p for p in self.parties}

        rows = [
            (by_id[pid], rolls.get(pid), allocation.get(pid, 0))
            for pid in sorted(
                set(rolls) | set(allocation),
                key=lambda pid: (-allocation.get(pid, 0), -shares.get(pid, 0.0)),
            )
            if pid in by_id
        ]
        self.page.show_dialog(dialogs.district_dialog(
            district, rows, shares, lambda _e: self.close_dialog()))

    def export_map_png(self) -> None:
        """Открывает диалог экспорта карты — тот же, что и у схемы зала."""
        conv = self.selected
        winners = self.service.district_winners(conv.id)
        colors = {p.id: p.color for p in self.parties}

        shapes = [
            (d.code, d.name, d.seats, colors.get(winners.get(d.id)))
            for d in self.service.project.districts if d.code
        ]

        won: dict[str, int] = {}
        for party_id in winners.values():
            won[party_id] = won.get(party_id, 0) + 1
        legend = sorted(
            ((p.name, p.color, won.get(p.id, 0), conv.seats.get(p.id, 0))
             for p in self.parties if conv.seats.get(p.id, 0) or won.get(p.id, 0)),
            key=lambda row: (row[3], row[2]), reverse=True,
        )
        background = map_image_path(self.service.path.parent)

        async def confirm(settings: dict) -> None:
            self.close_dialog()
            data = render_map_png(
                shapes, width=settings["width"],
                title=conv.name if settings["with_title"] else None,
                legend=legend if settings["with_legend"] else None,
                background=background,
            )

            file_name = (settings["file_name"] or "").strip() or suggest_file_name(
                conv.name, prefix="Карта")
            if not file_name.lower().endswith(".png"):
                file_name += ".png"

            saved = await self.file_picker.save_file(
                dialog_title="Экспорт карты в PNG",
                file_name=file_name,
                allowed_extensions=["png"],
                src_bytes=data,
            )
            if saved:
                self.toast("Карта сохранена.")

        self.page.show_dialog(dialogs.map_export_dialog(
            conv.name, shapes, legend, background,
            lambda settings: self.page.run_task(confirm, settings),
            lambda _e: self.close_dialog(),
        ))

    def load_support_file(self) -> None:
        """Загружает таблицу с населёнными пунктами и очками поддержки."""

        async def pick() -> None:
            files = await self.file_picker.pick_files(
                dialog_title="Таблица поддержки",
                allowed_extensions=["csv", "txt"],
                allow_multiple=False,
                with_data=True,
            )
            if not files:
                return
            try:
                result = read_support_file(files[0], self.service.project)
            except (OSError, ValueError) as error:
                self.toast(f"Не удалось прочитать файл: {error}", error=True)
                return

            if result.rows or result.city_rows:
                try:
                    self.service.import_support_table(result.rows, result.city_rows)
                except (ValidationError, StoreError) as error:
                    self.toast(str(error), error=True)
                    return

            self.render()
            # Диалог, а не тост, и всегда — не только при замечаниях: тост
            # исчезает сам, а тут человек видит, что именно занесено (сёла
            # отдельно от городов), и закрывает, когда прочитал.
            settlements = sum(len(v) for v in result.rows.values())
            self.page.show_dialog(dialogs.import_report_dialog(
                settlements, len(result.city_rows), result.warnings,
                lambda _e: self.close_dialog()))

        self.page.run_task(pick)

    def save_support_template(self) -> None:
        """Отдаёт таблицу поддержки под текущие округа, пункты и партии."""

        async def save() -> None:
            data = export_support_template(self.service.project)
            saved = await self.file_picker.save_file(
                dialog_title="Шаблон таблицы поддержки",
                file_name="Поддержка_шаблон.csv",
                allowed_extensions=["csv"],
                src_bytes=data,
            )
            if saved:
                self.toast("Шаблон сохранён.")

        self.page.run_task(save)

    def select_convocation(self, convocation_id: str) -> None:
        self.selected_convocation_id = convocation_id
        self.editing_archived = None
        self.render()

    def edit_archived(self) -> None:
        """Открыть архивный созыв на правку — без подтверждения, по решению из макетов."""
        self.editing_archived = self.selected.id
        self.render()
        self.toast("Правки сохранятся в этот же созыв — новый не создаётся.")

    # -- партии -------------------------------------------------------------

    def new_party(self) -> None:
        self._open_party_dialog(None)

    def edit_party(self, party: Party) -> None:
        self._open_party_dialog(party)

    def _open_party_dialog(self, party: Party | None) -> None:
        def save(name: str, color: str) -> str | None:
            try:
                if party:
                    # Сокращение больше не редактируется в этом диалоге —
                    # то, что уже было сохранено, остаётся как есть.
                    self.service.update_party(party.id, name=name, color=color, abbr=party.abbr)
                else:
                    self.service.create_party(name=name, color=color)
            except (ValidationError, StoreError) as error:
                return str(error)

            self.close_dialog()
            self.render()
            self.toast(f"Партия «{name.strip()}» "
                       f"{'обновлена' if party else 'создана'}.")
            return None

        self.page.show_dialog(dialogs.party_dialog(
            self.page, party, len(self.parties), save, lambda _e: self.close_dialog(),
            recent_colors=self.recent_colors,
            on_custom_color_picked=self._remember_recent_color,
        ))

    def _remember_recent_color(self, color: str) -> None:
        color = color.lower()
        if color in theme.PALETTE:
            return   # уже есть готовым цветом в палитре — незачем дублировать
        self.service.remember_recent_color(color)

    def delete_party(self, party: Party) -> None:
        footprint = self.service.party_footprint(party.id)
        usage = footprint["convocations"]

        def confirm(_event) -> None:
            try:
                self.service.delete_party(party.id)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()
            self.toast(f"Партия «{party.name}» удалена.")

        self.page.show_dialog(dialogs.delete_party_dialog(
            party, usage, fmt.plural, confirm, lambda _e: self.close_dialog(),
            footprint=footprint))

    # -- созывы -------------------------------------------------------------

    def rename_convocation(self) -> None:
        conv = self.selected

        def confirm(name: str) -> None:
            try:
                self.service.rename_convocation(conv.id, name)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()

        self.page.show_dialog(dialogs.rename_dialog(conv, confirm, lambda _e: self.close_dialog()))

    def new_convocation(self) -> None:
        if not self.parties:
            self.toast("Сначала создайте хотя бы одну партию.", error=True)
            return

        current = self.service.project.active_convocation

        def confirm(name: str) -> None:
            try:
                fresh = self.service.fix_convocation(name)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.selected_convocation_id = fresh.id
            self.editing_archived = None
            self.render()
            self.toast("Созыв зафиксирован, открыт новый состав.")

        self.page.show_dialog(dialogs.new_convocation_dialog(
            current,
            self.service.next_convocation_name(),
            self.used_seats(current),
            self.total_seats,
            len(self.distribution(current)),
            self.bar_segments(current),
            fmt.plural,
            confirm,
            lambda _e: self.close_dialog(),
        ))

    def delete_convocation(self, convocation: Convocation) -> None:
        def confirm(_event) -> None:
            try:
                fresh_active = self.service.delete_convocation(convocation.id)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            if self.selected_convocation_id == convocation.id:
                self.selected_convocation_id = fresh_active.id
            self.editing_archived = None
            self.render()
            self.toast(f"Созыв «{convocation.name}» удалён.")

        self.page.show_dialog(dialogs.delete_convocation_dialog(
            convocation, confirm, lambda _e: self.close_dialog()))

    def reset_election(self) -> None:
        """Убирает итоги выборов — состав снова набирается руками."""
        if not self.is_editable:
            return
        conv = self.selected

        def confirm(_event) -> None:
            try:
                self.service.clear_election(conv.id)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()
            self.toast("Итоги выборов убраны — места снова набираются руками.")

        self.page.show_dialog(dialogs._shell(
            "Сбросить выборы",
            [ft.Text(f"Убрать итоги выборов из состава «{conv.name}»?\n\n"
                     "Разбор округов и раскраска карты пропадут, места станут "
                     "нераспределёнными. Населённые пункты и очки поддержки "
                     "останутся — сбрасывается только розыгрыш.",
                     size=theme.fs(14), color=theme.TEXT)],
            [theme.secondary_button("Отмена", lambda _e: self.close_dialog()),
             theme.primary_button("Сбросить", confirm, danger=True)],
        ))

    def reset_seats(self) -> None:
        if not self.is_editable:
            return
        conv = self.selected

        def confirm(_event) -> None:
            try:
                self.service.reset_seats(conv.id)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()
            self.toast("Распределение сброшено.")

        self.page.show_dialog(dialogs._shell(
            "Сбросить распределение",
            [ft.Text(f"Обнулить места в составе «{conv.name}»?\n\n"
                     "Партии останутся в справочнике, все места станут "
                     "нераспределёнными.", size=theme.fs(14), color=theme.TEXT)],
            [theme.secondary_button("Отмена", lambda _e: self.close_dialog()),
             theme.primary_button("Сбросить", confirm, danger=True)],
        ))

    # -- экспорт ------------------------------------------------------------

    def export_png(self) -> None:
        conv = self.selected
        distribution = self.distribution(conv)
        if not distribution:
            self.toast("Нечего экспортировать: места не распределены.", error=True)
            return

        async def confirm(settings: dict) -> None:
            self.close_dialog()
            data = render_png(
                [LegendEntry(party.name, party.color, seats)
                 for party, seats in distribution],
                total_seats=self.total_seats,
                rows=self.service.project.rows,
                width=settings["width"],
                height=settings["height"],
                title=conv.name,
                with_legend=settings["with_legend"],
                with_title=settings["with_title"],
            )

            file_name = (settings["file_name"] or "").strip() or suggest_file_name(conv.name)
            if not file_name.lower().endswith(".png"):
                file_name += ".png"

            saved = await self.file_picker.save_file(
                dialog_title="Экспорт в PNG",
                file_name=file_name,
                allowed_extensions=["png"],
                src_bytes=data,
            )
            if saved:
                self.toast("Картинка сохранена.")

        self.page.show_dialog(dialogs.export_dialog(
            conv,
            self.total_seats,
            self.service.project.rows,
            [(party.abbr or party.name, party.color, seats)
             for party, seats in distribution],
            lambda settings: self.page.run_task(confirm, settings),
            lambda _e: self.close_dialog(),
        ))

    # -- прочее -------------------------------------------------------------

    def close_dialog(self) -> None:
        self.page.pop_dialog()

    def toast(self, message: str, error: bool = False) -> None:
        self.page.show_dialog(ft.SnackBar(
            content=ft.Text(message, size=theme.fs(13), color=theme.BG),
            bgcolor=theme.ACCENT_2_700 if error else theme.NEUTRAL_900,
            duration=6000 if error else 4000,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=theme.RADIUS),
        ))
