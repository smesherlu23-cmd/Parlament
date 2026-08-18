"""Справочник партий: таблица со всеми партиями и действиями над ними.

Правки здесь расходятся по всем созывам — партия хранится один раз, созывы
ссылаются на неё по идентификатору.
"""

from __future__ import annotations

import flet as ft

from . import format as fmt, theme


class PartiesView:
    """Экран справочника. Собирается заново при каждом изменении списка."""

    def __init__(self, app):
        self.app = app

    def build(self) -> ft.Control:
        parties = self.app.parties
        content = self._table() if parties else self._empty()

        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=40, vertical=24),
            content=ft.Column([
                ft.Row([
                    ft.Text(fmt.pluralize(len(parties), fmt.PARTIES_COUNT),
                            size=22, font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                    ft.Text("Цвет и название обновляются во всех созывах, "
                            "где партия участвует.",
                            size=13, color=theme.NEUTRAL_600),
                ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.END),
                ft.Container(content, expand=True, padding=ft.Padding.only(top=16)),
            ], spacing=0, expand=True),
        )

    def _table(self) -> ft.Control:
        seats_now = self.app.selected.seats

        def header(text: str) -> ft.DataColumn:
            return ft.DataColumn(ft.Text(
                text.upper(), size=11, color=theme.NEUTRAL_600,
                style=ft.TextStyle(letter_spacing=0.9),
            ))

        rows = []
        for party in self.app.parties:
            rows.append(ft.DataRow(cells=[
                ft.DataCell(theme.swatch(party.color, 20)),
                ft.DataCell(ft.Text(party.name, size=14,
                                    font_family=theme.FONT_SEMIBOLD, color=theme.TEXT)),
                ft.DataCell(ft.Text(party.abbr or "—", size=14, color=theme.NEUTRAL_700)),
                ft.DataCell(ft.Text(party.color.upper(), size=12.5,
                                    font_family="monospace", color=theme.NEUTRAL_700)),
                ft.DataCell(ft.Text(str(self._convocation_count(party.id)),
                                    size=14, color=theme.NEUTRAL_700)),
                ft.DataCell(ft.Text(str(seats_now.get(party.id, 0)), size=14, color=theme.TEXT)),
                ft.DataCell(ft.Row([
                    theme.ghost_button("Изменить",
                                       lambda _e, p=party: self.app.edit_party(p)),
                    theme.ghost_button("Удалить",
                                       lambda _e, p=party: self.app.delete_party(p),
                                       danger=True),
                ], spacing=12, alignment=ft.MainAxisAlignment.END)),
            ]))

        table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("")),
                header("Название"),
                header("Сокращение"),
                header("Цвет"),
                header("Созывов"),
                header("Мест сейчас"),
                ft.DataColumn(ft.Text("")),
            ],
            rows=rows,
            expand=True,                     # таблица во всю ширину экрана
            heading_row_height=40,
            data_row_min_height=48,
            divider_thickness=0,             # линии рисуем сами, тоньше
            horizontal_lines=ft.BorderSide(1, "#14201e1d"),
            column_spacing=24,
            bgcolor=ft.Colors.TRANSPARENT,
            # Материаловская тема иначе подкрашивает строки своим фоном.
            heading_row_color=ft.Colors.TRANSPARENT,
            data_row_color={
                ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                ft.ControlState.HOVERED: "#0a201e1d",
            },
            heading_text_style=ft.TextStyle(size=11, color=theme.NEUTRAL_600,
                                            letter_spacing=0.9),
        )
        # Row задаёт таблице ширину: сама по себе DataTable сжимается по
        # содержимому, и линии строк обрывались бы посреди экрана.
        return ft.Column([ft.Row([ft.Container(table, expand=True)])],
                         scroll=ft.ScrollMode.AUTO, expand=True)

    def _convocation_count(self, party_id: str) -> int:
        """В скольких созывах партия имеет места."""
        return sum(1 for conv in self.app.service.project.convocations
                   if conv.seats.get(party_id, 0) > 0)

    def _empty(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.only(top=40),
            alignment=ft.Alignment.TOP_CENTER,
            content=ft.Column([
                ft.Text("Справочник пуст. Начните с 3–6 партий — их можно будет "
                        "переименовать и перекрасить в любой момент.",
                        size=14, color=theme.NEUTRAL_700, text_align=ft.TextAlign.CENTER),
                theme.primary_button("Новая партия", lambda _e: self.app.new_party()),
            ], spacing=16, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
        )
