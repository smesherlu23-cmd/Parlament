"""Экран «Выборы»: ввод голосов по округам и пересчёт состава.

Таблица «округ × партия»: строка на каждый округ карты, столбец на каждую
партию справочника. Заполнять можно руками или загрузить таблицу файлом —
кнопка «Загрузить таблицу» разбирает тот же формат, что описан в
`votes_import`.

Числа не применяются к проекту по ходу набора: пока идёт ввод, они живут
только в полях. Состав пересобирается разом по кнопке «Посчитать» — иначе
наполовину введённые выборы успели бы перекроить схему в зале.
"""

from __future__ import annotations

import flet as ft

from ..elections import allocate_seats
from . import theme
from .mount import push

#: Ширина поля под голоса. Шестизначные числа влезают без обрезки.
_VOTE_WIDTH = 92
_NAME_WIDTH = 210
_PREVIEW_WIDTH = 240


class ElectionsView:
    """Таблица результатов. Живёт ровно столько, сколько открыт экран."""

    def __init__(self, app):
        self.app = app
        self.service = app.service
        #: `{district_id: {party_id: ft.TextField}}` — поля ввода.
        self.fields: dict[str, dict[str, ft.TextField]] = {}
        #: Подписи с раскладом мест справа от каждой строки.
        self.previews: dict[str, ft.Text] = {}

    # -- сборка -------------------------------------------------------------

    def build(self) -> ft.Control:
        if not self.app.parties:
            return self._needs_parties()
        if not self.service.project.districts:
            return self._needs_districts()

        conv = self.app.selected
        votes = conv.votes

        header = ft.Row(
            [
                ft.Container(theme.label("Округ"), width=_NAME_WIDTH),
                ft.Container(theme.label("Мест"), width=52),
                *[
                    ft.Container(
                        ft.Row([
                            theme.swatch(party.color, 10),
                            # Растягиваем подпись внутри строки: без этого она
                            # не знает своей ширины, не сокращается многоточием
                            # и наезжает на соседний столбец.
                            ft.Container(
                                ft.Text(party.abbr or party.name, size=theme.fs(11),
                                        color=theme.NEUTRAL_600, no_wrap=True,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                expand=True,
                            ),
                        ], spacing=5),
                        width=_VOTE_WIDTH,
                        tooltip=party.name,
                    )
                    for party in self.app.parties
                ],
                ft.Container(theme.label("Расклад"), width=_PREVIEW_WIDTH),
            ],
            spacing=10,
        )

        rows: list[ft.Control] = []
        last_region = None
        for district in self.service.project.districts:
            if district.region != last_region:
                last_region = district.region
                rows.append(ft.Container(
                    padding=ft.Padding.only(top=14, bottom=4),
                    content=theme.label(district.region or "Прочие"),
                ))
            rows.append(self._district_row(district, votes.get(district.id, {})))

        return ft.Column([
            ft.Container(
                padding=ft.Padding.only(left=28, right=28, top=18, bottom=8),
                content=ft.Column([
                    ft.Text(
                        f"{len(self.service.project.districts)} округов · "
                        f"{self.service.project.total_seats} мест · {conv.name}",
                        size=theme.fs(15), font_family=theme.FONT_SEMIBOLD, color=theme.TEXT,
                    ),
                ], spacing=4, tight=True),
            ),
            ft.Container(
                expand=True,
                padding=ft.Padding.only(left=28, right=28, bottom=18),
                # Две прокрутки: вниз по округам и вбок — таблица с десятком
                # партий шире окна, и без этого правые столбцы недостижимы.
                content=ft.Row([
                    ft.Column(
                        [header, ft.Divider(height=1, color=theme.DIVIDER), *rows],
                        spacing=4, scroll=ft.ScrollMode.AUTO, expand=True, tight=False,
                    ),
                ], scroll=ft.ScrollMode.AUTO, expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START),
            ),
        ], spacing=0, expand=True)

    def _district_row(self, district, votes: dict[str, int]) -> ft.Control:
        fields: dict[str, ft.TextField] = {}
        cells: list[ft.Control] = [
            ft.Container(
                ft.Text(district.name, size=theme.fs(14), color=theme.TEXT, no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS, tooltip=district.name),
                width=_NAME_WIDTH,
            ),
            ft.Container(
                ft.Text(str(district.seats), size=theme.fs(14),
                        font_family=theme.FONT_SEMIBOLD, color=theme.NEUTRAL_700),
                width=52,
            ),
        ]

        for party in self.app.parties:
            value = votes.get(party.id, 0)
            field = theme.text_field(
                str(value) if value else "",
                width=_VOTE_WIDTH,
                text_align=ft.TextAlign.RIGHT,
                keyboard_numeric=True,
            )
            field.data = (district.id, party.id)
            field.on_change = self._on_change
            fields[party.id] = field
            cells.append(field)

        preview = ft.Text(size=theme.fs(12), color=theme.NEUTRAL_700, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS)
        self.previews[district.id] = preview
        cells.append(ft.Container(preview, width=_PREVIEW_WIDTH))

        self.fields[district.id] = fields
        self._refresh_preview(district.id, live=False)

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=3),
            content=ft.Row(cells, spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    # -- ввод ---------------------------------------------------------------

    def _on_change(self, event) -> None:
        """Чистит ввод и обновляет расклад строки.

        Проект при этом не трогается: выборы применяются целиком по кнопке.
        """
        field = event.control
        cleaned = "".join(ch for ch in (field.value or "") if ch.isdigit())
        if cleaned != field.value:
            field.value = cleaned
            push(field)
        district_id, _party_id = field.data
        self._refresh_preview(district_id)

    def _refresh_preview(self, district_id: str, live: bool = True) -> None:
        """Показывает, как голоса строки лягут в места округа."""
        district = self.service.project.district(district_id)
        if district is None:
            return
        allocation = allocate_seats(self.collect_district(district_id), district.seats)
        preview = self.previews.get(district_id)
        if preview is None:
            return

        if not allocation:
            preview.value = "—"
            preview.color = theme.NEUTRAL_600
        else:
            names = {p.id: (p.abbr or p.name) for p in self.app.parties}
            preview.value = "  ".join(
                f"{names.get(pid, '?')} {seats}"
                for pid, seats in sorted(allocation.items(), key=lambda kv: -kv[1])
            )
            preview.color = theme.TEXT
        if live:
            push(preview)

    # -- сбор данных --------------------------------------------------------

    def collect_district(self, district_id: str) -> dict[str, int]:
        return {
            party_id: int(field.value)
            for party_id, field in self.fields.get(district_id, {}).items()
            if (field.value or "").strip().isdigit() and int(field.value) > 0
        }

    def collect(self) -> dict[str, dict[str, int]]:
        """Всё введённое — в виде, который принимает `run_election`."""
        result = {}
        for district_id in self.fields:
            votes = self.collect_district(district_id)
            if votes:
                result[district_id] = votes
        return result

    def fill(self, votes: dict[str, dict[str, int]]) -> None:
        """Раскладывает загруженную таблицу по полям, затирая прежний ввод."""
        for district_id, fields in self.fields.items():
            per_party = votes.get(district_id, {})
            for party_id, field in fields.items():
                value = per_party.get(party_id, 0)
                field.value = str(value) if value else ""
                push(field)
            self._refresh_preview(district_id)

    # -- пустые состояния ---------------------------------------------------

    def _needs_parties(self) -> ft.Control:
        return self._notice("Сначала создайте партии",
                            "Новая партия", self.app.show_parties)

    def _needs_districts(self) -> ft.Control:
        return self._notice(
            "В проекте нет округов",
            "К парламенту", self.app.show_parliament,
            hint="Округа появляются в новых проектах. Этот создан до карты — "
                 "места в нём распределяются вручную.",
        )

    def _notice(self, title: str, button: str, action, hint: str | None = None) -> ft.Control:
        content: list[ft.Control] = [
            ft.Text(title, size=theme.fs(18), font_family=theme.FONT_SEMIBOLD,
                    color=theme.TEXT),
        ]
        if hint:
            content.append(ft.Container(
                width=430,
                content=ft.Text(hint, size=theme.fs(13), color=theme.NEUTRAL_700,
                                text_align=ft.TextAlign.CENTER),
            ))
        content.append(theme.primary_button(button, lambda _e: action()))
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(content, spacing=16,
                              alignment=ft.MainAxisAlignment.CENTER,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )
