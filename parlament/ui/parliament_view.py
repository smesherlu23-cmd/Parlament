"""Главный экран: список созывов, схема зала и распределение мест.

Три колонки. Слева — созывы, по клику переключается открытый; в центре —
полукруглая схема с легендой и строкой про большинство; справа — правая
панель, которая выглядит по-разному в трёх случаях: места набираются
руками, места посчитаны выборами (тогда полей ввода нет — их источник
округа, а не человек), созыв лежит в истории и открыт только на чтение.

Экран держит свои контролы в полях, потому что обновляет их точечно: ввод
числа мест меняет схему, легенду, сводку и список созывов, но не
пересобирает окно целиком — на схеме 240 фигур, и полная пересборка на
каждое нажатие клавиши была бы заметна.
"""

from __future__ import annotations

import flet as ft

from ..model import Convocation
from ..service import ValidationError
from ..store import StoreError
from . import dialogs, format as fmt, theme
from .mount import push
from .seat_chart import SeatChart, chart_height_for_width


class ParliamentView:
    """Схема зала, список созывов и правая панель с местами."""

    def __init__(self, app):
        self.app = app
        self.service = app.service

    # -- сборка -------------------------------------------------------------

    def build(self) -> ft.Control:
        app = self.app
        conv = app.selected
        has_parties = bool(app.parties)

        self.chart = SeatChart(app.total_seats, self.service.project.rows,
                               [(p.color, s) for p, s in app.distribution(conv)])
        self.legend_row = ft.Row(wrap=True, spacing=26, run_spacing=8)
        self.majority_text = ft.Text(size=theme.fs(12))
        self.conv_list = ft.Column(spacing=6, tight=True)
        self.stage_meta = ft.Text(size=theme.fs(13), color=theme.NEUTRAL_700)

        center = self._build_stage(conv) if has_parties else self._build_empty_stage()
        right = self._build_seat_rail(conv) if has_parties else self._build_empty_rail()

        self.refresh_conv_list(live=False)
        if has_parties:
            self.refresh_derived(live=False)

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
                    "+ Новый созыв", lambda _e: self.app.new_convocation(),
                    disabled=not self.app.parties,
                ),
            ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True),
        )

    def refresh_conv_list(self, live: bool = True) -> None:
        app = self.app
        many = len(self.service.project.convocations) > 1
        cards = []
        for conv in app.convocations:
            selected = conv.id == app.selected.id
            used = app.used_seats(conv)
            note = "Редактируется" if not conv.is_fixed else f"{used} из {app.total_seats} мест"

            header = [ft.Container(
                ft.Text(conv.name, size=theme.fs(14), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, no_wrap=True),
                expand=True,
            )]
            # Единственный созыв удалить нельзя — история не может опустеть,
            # так что кнопке тогда просто нечего делать.
            if many:
                header.append(theme.icon_button(
                    ft.Icons.CLOSE, lambda _e, c=conv: app.delete_convocation(c),
                    danger=True, tooltip="Удалить созыв", size=13,
                ))

            cards.append(ft.Container(
                bgcolor=theme.ACCENT_100 if selected else ft.Colors.TRANSPARENT,
                padding=ft.Padding.symmetric(horizontal=10, vertical=9),
                border_radius=theme.RADIUS,
                animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
                data=conv.id,
                on_click=lambda e: app.select_convocation(e.control.data),
                ink=True,
                content=ft.Column([
                    ft.Row(header, spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    dialogs.seat_bar(app.bar_segments(conv)),
                    ft.Text(note, size=theme.fs(11),
                            color=theme.ACCENT_700 if not conv.is_fixed else theme.NEUTRAL_600),
                ], spacing=6, tight=True),
            ))
        self.conv_list.controls = cards
        if live:
            push(self.conv_list)

    def _build_stage(self, conv: Convocation) -> ft.Control:
        app = self.app
        head: list[ft.Control] = [theme.heading(conv.name)]
        if app.is_editable:
            head.append(theme.ghost_button("Переименовать",
                                           lambda _e: app.rename_convocation()))
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
                        ft.Text(f"Большинство — {fmt.pluralize(app.majority_seats, fmt.SEATS)}.",
                                size=theme.fs(12), color=theme.NEUTRAL_600),
                        self.majority_text,
                    ], spacing=10),
                ),
            ], spacing=0, expand=True),
        )

    def _build_empty_stage(self) -> ft.Control:
        app = self.app
        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=28, vertical=26),
            content=ft.Column([
                ft.Container(
                    SeatChart(app.total_seats, self.service.project.rows, [],
                              opacity=0.5, height=chart_height_for_width(560)),
                    width=560,
                ),
                ft.Text("Партий пока нет", size=theme.fs(22),
                        font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                theme.primary_button("Создать первую партию", lambda _e: app.show_parties()),
            ],
                spacing=16,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_seat_rail(self, conv: Convocation) -> ft.Control:
        app = self.app
        if not app.is_editable:
            return self._build_readonly_rail(conv)
        if conv.has_election:
            return self._build_election_rail(conv)

        self.seat_fields: dict[str, ft.TextField] = {}
        rows: list[ft.Control] = []
        for party in app.parties:
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

        self.used_text = ft.Text(size=theme.fs(16), font_family=theme.FONT_SEMIBOLD,
                                 color=theme.TEXT)
        self.remaining_text = ft.Text(size=theme.fs(16), font_family=theme.FONT_SEMIBOLD)
        self.progress = ft.ProgressBar(bgcolor=theme.NEUTRAL_300, color=theme.ACCENT,
                                       height=6, border_radius=0, value=0)
        self.remaining_note = ft.Text(size=theme.fs(12))
        self.reset_button = theme.secondary_button(
            "Сбросить распределение", lambda _e: app.reset_seats(), expand=True)

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
                        ft.Row([ft.Text("Распределено", size=theme.fs(13),
                                        color=theme.NEUTRAL_700),
                                ft.Container(expand=True), self.used_text],
                               vertical_alignment=ft.CrossAxisAlignment.END),
                        ft.Row([ft.Text("Остаток", size=theme.fs(13),
                                        color=theme.NEUTRAL_700),
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
                                                     lambda _e: app.export_png(),
                                                     expand=True)]),
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
        app = self.app
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
            for party, seats in app.distribution(conv)
        ]
        districts = len(conv.results)

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
                                                       lambda _e: app.reset_election(),
                                                       expand=True)]),
                        ft.Row([theme.primary_button("Экспортировать в PNG",
                                                     lambda _e: app.export_png(),
                                                     expand=True)]),
                    ], spacing=8, tight=True),
                ),
            ], spacing=0, expand=True),
        )

    def _build_readonly_rail(self, conv: Convocation) -> ft.Control:
        app = self.app
        rows = [
            ft.Container(
                padding=ft.Padding.symmetric(vertical=7),
                border=ft.Border.only(bottom=ft.BorderSide(1, "#14201e1d")),
                content=ft.Row([
                    theme.swatch(party.color, 12),
                    ft.Container(ft.Text(party.name, size=theme.fs(14), color=theme.TEXT,
                                         no_wrap=True),
                                 expand=True),
                    ft.Text(str(seats), size=theme.fs(15),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                ], spacing=10),
            )
            for party, seats in app.distribution(conv)
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
                        "Экспортировать в PNG", lambda _e: app.export_png(), expand=True)]),
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

    def refresh_derived(self, live: bool = True) -> None:
        """Пересчитывает всё, что зависит от распределения мест.

        :param live: при сборке экрана — False: контролы ещё не на странице,
                     и отправлять их рано; страница обновится целиком в `render`.
        """
        app = self.app
        conv = app.selected
        distribution = app.distribution(conv)
        used = app.used_seats(conv)
        remaining = app.total_seats - used

        self.chart.set_data(app.total_seats, self.service.project.rows,
                            [(p.color, s) for p, s in distribution])

        if distribution:
            self.legend_row.controls = [
                ft.Row([
                    theme.swatch(party.color, 11),
                    ft.Text(party.name, size=theme.fs(13), color=theme.TEXT),
                    ft.Text(str(seats), size=theme.fs(13),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                    ft.Text(fmt.percent(seats, app.total_seats), size=theme.fs(13),
                            color=theme.NEUTRAL_600),
                ], spacing=8, tight=True)
                for party, seats in distribution
            ]
        else:
            self.legend_row.controls = []

        largest = distribution[0] if distribution else None
        if largest and largest[1] >= app.majority_seats:
            self.majority_text.value = f"{largest[0].name} — абсолютное большинство"
            self.majority_text.color = theme.ACCENT_2_700
        elif largest:
            self.majority_text.value = (f"Крупнейшая фракция: {largest[0].name} "
                                        f"({largest[1]}), большинства нет")
            self.majority_text.color = theme.NEUTRAL_700
        else:
            self.majority_text.value = "Мест никому не отдано."
            self.majority_text.color = theme.NEUTRAL_700

        if not app.manual_seats and conv.has_election:
            self.stage_meta.value = (
                f"Выборы: {fmt.pluralize(len(conv.results), fmt.DISTRICTS)} "
                f"· {used} из {app.total_seats} мест")
        if app.manual_seats:
            self.stage_meta.value = f"Распределено {used} из {app.total_seats}"
            self.used_text.value = f"{used} / {app.total_seats}"
            self.remaining_text.value = str(remaining)
            self.remaining_text.color = (theme.NEUTRAL_700 if remaining == 0
                                         else theme.ACCENT_2_700)
            self.progress.value = used / app.total_seats
            self.remaining_note.value = (
                "Все места распределены." if remaining == 0 else
                f"Осталось распределить {fmt.pluralize(remaining, fmt.SEATS)}."
            )
            self.remaining_note.color = (theme.NEUTRAL_700 if remaining == 0
                                         else theme.ACCENT_2_700)
            self.reset_button.disabled = used == 0

        self.refresh_conv_list(live=live)
        if live:
            for control in (self.chart, self.legend_row, self.majority_text,
                            self.stage_meta):
                push(control)
            if app.manual_seats:
                for control in (self.used_text, self.remaining_text, self.progress,
                                self.remaining_note, self.reset_button):
                    push(control)

    # -- правка мест --------------------------------------------------------

    def _on_seat_change(self, event: ft.ControlEvent) -> None:
        app = self.app
        field = event.control
        party_id = field.data
        conv = app.selected

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
        value = max(0, min(requested, app.total_seats - others))

        try:
            self.service.set_seats(conv.id, party_id, value)
        except (ValidationError, StoreError) as error:
            app.toast(str(error), error=True)
            return

        if value != requested:
            field.value = str(value)
            push(field)
        self.refresh_derived()

    def _on_seat_blur(self, event: ft.ControlEvent) -> None:
        """Пустое поле после ухода фокуса — это ноль, а не «не задано»."""
        field = event.control
        if (field.value or "").strip() == "":
            field.value = "0"
            push(field)
            try:
                self.service.set_seats(self.app.selected.id, field.data, 0)
            except (ValidationError, StoreError) as error:
                self.app.toast(str(error), error=True)
                return
            self.refresh_derived()
