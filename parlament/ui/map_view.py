"""Экран «Карта»: округа архипелага, покрашенные по победителям выборов.

Слева — сама карта, справа — сводка: сколько округов и мест взяла каждая
партия. Клик по округу показывает его расклад: бросок со слагаемыми,
проценты и места.
"""

from __future__ import annotations

import flet as ft

from . import theme
from .map_chart import MapChart, map_image_path


class MapView:
    """Сборка экрана карты для текущего созыва."""

    def __init__(self, app):
        self.app = app
        self.service = app.service

    def build(self) -> ft.Control:
        if not self.service.project.districts:
            return self._no_districts()

        conv = self.app.selected
        winners = self.service.district_winners(conv.id) if conv.has_election else {}
        colors = {p.id: p.color for p in self.app.parties}

        # Карта рисуется полигонами округов; связь «код -> округ» нужна,
        # чтобы по клику вернуться от нарисованной фигуры к данным.
        self.by_code = {d.code: d for d in self.service.project.districts if d.code}
        shapes = [
            (d.code, d.name, d.seats, colors.get(winners.get(d.id)))
            for d in self.service.project.districts if d.code
        ]
        self.app.map_chart = MapChart(
            districts=shapes,
            on_pick=self._pick,
            background=map_image_path(self.service.path.parent),
        )

        return ft.Row(
            [
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=20, right=12, top=16, bottom=16),
                    content=ft.Column([
                        self._heading(conv, winners),
                        ft.Container(self.app.map_chart, expand=True,
                                     padding=ft.Padding.only(top=10)),
                    ], spacing=0, expand=True),
                ),
                self._rail(conv, winners),
            ],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _pick(self, code: int) -> None:
        district = self.by_code.get(code)
        if district is not None:
            self.app.show_district(district.id)

    def _heading(self, conv, winners: dict[str, str]) -> ft.Control:
        districts = self.service.project.districts
        if winners:
            note = (f"{len(winners)} из {len(districts)} округов · "
                    f"{sum(conv.seats.values())} из {self.service.project.total_seats} мест")
        elif conv.has_election:
            # Розыгрыш был, но модификаторы увели всех в ноль: голосов нет ни
            # у кого, и красить нечего — сказать об этом честнее, чем делать
            # вид, что выборов не было.
            note = f"{len(districts)} округов · ни одна партия не набрала голосов"
        else:
            note = f"{len(districts)} округов · выборы не проводились"
        return ft.Row([
            theme.heading(conv.name, size=22),
            ft.Container(expand=True),
            ft.Text(note, size=theme.fs(13), color=theme.NEUTRAL_700),
        ], vertical_alignment=ft.CrossAxisAlignment.END)

    def _rail(self, conv, winners: dict[str, str]) -> ft.Control:
        rows: list[ft.Control] = []

        if winners:
            # Сколько округов и мест взяла каждая партия — по убыванию мест.
            won = {}
            for party_id in winners.values():
                won[party_id] = won.get(party_id, 0) + 1
            ranked = sorted(
                ((p, conv.seats.get(p.id, 0), won.get(p.id, 0)) for p in self.app.parties
                 if conv.seats.get(p.id, 0) or won.get(p.id, 0)),
                key=lambda item: (item[1], item[2]), reverse=True,
            )
            for party, seats, districts_won in ranked:
                rows.append(ft.Container(
                    padding=ft.Padding.symmetric(vertical=7),
                    border=ft.Border.only(bottom=ft.BorderSide(1, "#14201e1d")),
                    content=ft.Row([
                        theme.swatch(party.color, 12),
                        ft.Container(
                            ft.Text(party.name, size=theme.fs(14), color=theme.TEXT,
                                    no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                                    tooltip=party.name),
                            expand=True,
                        ),
                        ft.Text(f"{districts_won} окр.", size=theme.fs(12),
                                color=theme.NEUTRAL_600),
                        ft.Text(str(seats), size=theme.fs(15),
                                font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                    ], spacing=10),
                ))
        else:
            rows.append(ft.Container(
                padding=ft.Padding.only(top=8),
                content=ft.Text(
                    "Выборы разыграны, но ни одна партия не набрала голосов: "
                    "штрафы увели всех в ноль. Клик по округу покажет разбор."
                    if conv.has_election else
                    "Раздайте очки поддержки на экране «Поддержка», затем "
                    "нажмите «Выборы» и разыграйте их — карта раскрасится "
                    "в цвета победителей.",
                    size=theme.fs(13), color=theme.NEUTRAL_700,
                ),
            ))

        return ft.Container(
            width=theme.RAIL_RIGHT_WIDTH,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            border=ft.Border.only(left=ft.BorderSide(1, theme.DIVIDER)),
            content=ft.Column([
                theme.label("Итоги по округам"),
                ft.Container(
                    ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    padding=ft.Padding.only(top=12),
                ),
            ], spacing=0, expand=True),
        )

    def _no_districts(self) -> ft.Control:
        """Проект начат до появления карты — предлагаем завести округа.

        Молча при загрузке файла их не добавляем: это меняет размер палаты,
        поэтому решение остаётся за пользователем. Но и тупика тут быть не
        должно — отсюда кнопка, а не одно объяснение.
        """
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column([
                ft.Text("В проекте нет округов", size=theme.fs(18),
                        font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                ft.Container(
                    width=440,
                    content=ft.Text(
                        "Этот проект начат до появления карты, поэтому места в нём "
                        "распределяются вручную. Округа можно добавить — тогда "
                        "станут доступны выборы и раскраска карты.",
                        size=theme.fs(13), color=theme.NEUTRAL_700,
                        text_align=ft.TextAlign.CENTER),
                ),
                theme.primary_button("Взять округа с карты",
                                     lambda _e: self.app.adopt_map_districts()),
            ], spacing=16,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )
