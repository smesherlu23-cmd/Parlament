"""Сборка приложения: состояние, шапка, навигация, действия.

Логика живёт в `service` и вызывается напрямую — здесь нет ни сети, ни
межпроцессного обмена: Flet-приложение и бизнес-логика работают в одном
процессе Python.

Сами экраны сюда не входят: каждый собирает свой класс (`ParliamentView`,
`PartiesView`, `MapView`, `ElectionsView`, `SupportView`), а здесь остаётся
то, что общее для всех, — выборки по текущему созыву, шапка с кнопками,
переключение экранов и действия, которые открывают диалоги и зовут сервис.
Экраны, которые держат поля ввода или обновляют себя точечно, живут в полях
(`self.parliament`, `self.elections`): с них потом собираются введённые
числа.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from .. import coalitions, model
from ..model import Convocation, Party
from ..service import EMBLEM_SUFFIXES, ParlamentService, ValidationError
from ..store import StoreError
from . import dialogs, format as fmt, theme
from .export import LegendEntry, render_png, suggest_file_name
from .elections_view import ElectionsView
from .map_chart import map_image_path
from .map_export import render_map_png
from .mount import push
from .support_export import is_image, map_image as support_map_image
from .support_export import render_support_png
from .map_view import MapView
from .parliament_view import ParliamentView
from .parties_view import PartiesView
from .stats_export import render_stats_png
from .stats_view import StatsView
from .support_view import SupportView
from .support_file import (export_support_template, read_picked_bytes,
                           read_support_file)


class ParlamentApp:
    """Окно приложения: шапка, три колонки, диалоги."""

    def __init__(self, page: ft.Page, service: ParlamentService):
        self.page = page
        self.service = service

        self.view = "parliament"   # parliament | parties | map | elections | support | stats
        #: Заполняются при сборке соответствующего экрана.
        self.map_chart = None
        self.elections = None
        self.parliament = None
        #: Какие округа раскрыты на экране поддержки — чтобы список не
        #: схлопывался после каждой правки очков.
        self.support_opened: set[str] = set()
        self.selected_convocation_id: str | None = None
        self.editing_archived: str | None = None
        #: Чью статистику смотрим: None — общая, иначе id партии. Живёт в
        #: приложении, а не в экране: экран пересобирается на каждое
        #: переключение, и выбор бы каждый раз слетал.
        self.stats_party: str | None = None

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
        # Иконка окна: без неё и в заголовке, и в панели задач стоит значок
        # Flet. Сборка берёт свою иконку из assets/icon.png сама, но на окно
        # это не распространяется — его иконку задаёт только программа, и
        # только файлом .ico.
        if theme.WINDOW_ICON.exists():
            page.window.icon = str(theme.WINDOW_ICON)

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

    def blocs(self, conv: Convocation) -> list:
        """Состав блоками: коалиция идёт одной колонкой, крупнейшая слева.

        Схема, легенда и строка большинства читают состав отсюда, а не из
        `distribution`: партия, вошедшая в блок, не должна стоять на дуге
        отдельно от союзников — иначе плёнка накрыла бы чужие места.
        """
        return coalitions.blocs(self.parties, conv.seats, conv.coalitions,
                                self.service.vote_shares(conv.id))

    def map_legend(self, conv: Convocation) -> list[tuple]:
        """Строки легенды карты: `(имя, цвет, округов, мест, доля голосов)`.

        Отдельно от `blocs()` — карта красится по победителю округа, а не по
        коалициям: партия внутри блока на карте видна своим цветом, а не
        цветом плёнки. Доля голосов та же, что и в легенде схемы (`None`,
        если выборов не было).
        """
        winners = self.service.district_winners(conv.id)
        won: dict[str, int] = {}
        for party_id in winners.values():
            won[party_id] = won.get(party_id, 0) + 1
        votes = self.service.vote_shares(conv.id)
        return sorted(
            ((p.name, p.color, won.get(p.id, 0), conv.seats.get(p.id, 0), votes.get(p.id))
             for p in self.parties if conv.seats.get(p.id, 0) or won.get(p.id, 0)),
            key=lambda row: (row[3], row[2]), reverse=True,
        )

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
        elif self.view == "stats":
            self.body.content = StatsView(self).build()
        elif self.view == "elections":
            # Экран выборов держит поля ввода, поэтому живёт в поле: с него
            # потом собираются введённые поправки.
            self.elections = ElectionsView(self)
            self.body.content = self.elections.build()
        else:
            # Главный экран тоже живёт в поле: он обновляет схему и сводку
            # точечно, не пересобирая окно на каждое нажатие клавиши.
            self.parliament = ParliamentView(self)
            self.body.content = self.parliament.build()
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
        elif self.view == "stats":
            left = [
                theme.ghost_button("← К парламенту", lambda _e: self.show_parliament()),
                ft.Text("СТАТИСТИКА", size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                        color=theme.TEXT, style=ft.TextStyle(letter_spacing=2.1)),
            ]
            right = [
                theme.secondary_button(
                    "Экспорт в PNG", lambda _e: self.export_stats_png(),
                    disabled=not (self.selected.has_election and self.parties)),
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
                theme.secondary_button("Экспорт поддержки",
                                       lambda _e: self.export_support_png(),
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
                theme.secondary_button("Поддержка", lambda _e: self.show_support()),
                theme.secondary_button("Карта", lambda _e: self.show_map()),
                # Показывается всегда, но до выборов нажимать не на что:
                # статистика целиком считается из разбора по округам.
                theme.secondary_button(
                    "Статистика", lambda _e: self.show_stats(),
                    disabled=not self.selected.has_election,
                    tooltip=None if self.selected.has_election
                    else "Появится после выборов"),
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

    def show_stats(self) -> None:
        self.view = "stats"
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
        island = self.elections.collect_island()
        try:
            self.service.roll_election(self.selected.id, modifiers,
                                       national=national, island=island)
        except (ValidationError, StoreError) as error:
            self.toast(str(error), error=True)
            return

        conv = self.selected
        filled = len(conv.results)
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
        results = conv.results.get(district_id, {})
        shares = self.service.district_shares(conv.id, district_id)
        by_id = {p.id: p for p in self.parties}

        rows = [
            (by_id[pid], results.get(pid), allocation.get(pid, 0))
            for pid in sorted(
                set(results) | set(allocation),
                key=lambda pid: (-allocation.get(pid, 0), -shares.get(pid, 0.0)),
            )
            if pid in by_id
        ]
        self.page.show_dialog(dialogs.district_dialog(
            district, rows, shares, lambda _e: self.close_dialog(),
            population=self.service.district_population(district_id)))

    def export_map_png(self) -> None:
        """Открывает диалог экспорта карты — тот же, что и у схемы зала."""
        conv = self.selected
        winners = self.service.district_winners(conv.id)
        colors = {p.id: p.color for p in self.parties}

        shapes = [
            (d.code, d.name, d.seats, colors.get(winners.get(d.id)))
            for d in self.service.project.districts if d.code
        ]

        legend = self.map_legend(conv)
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

    def export_stats_png(self) -> None:
        """Выгружает статистику — ровно ту, что открыта на экране."""
        conv = self.selected
        if not conv.has_election or not self.parties:
            self.toast("Статистика появится после выборов.", error=True)
            return

        party_id = self.stats_party
        party = self.service.project.party(party_id) if party_id else None
        if party_id and party is None:
            party_id = None

        islands = self.service.island_stats(conv.id)
        attribution = self.service.attribution(conv.id)
        parties = [(p.id, p.name, p.abbr, p.color) for p in self.parties]
        ground = self.service.battlegrounds(conv.id, party_id) if party_id else None
        summary = None
        if party is not None:
            summary = (conv.seats.get(party.id, 0), self.total_seats,
                       self.service.vote_shares(conv.id).get(party.id, 0.0))

        async def confirm(settings: dict) -> None:
            self.close_dialog()
            data = render_stats_png(
                conv.name, parties, islands, attribution,
                party_id=party_id, summary=summary, ground=ground,
                width=settings["width"],
                emblem=self.service.party_emblem_path(party.id) if party else None,
            )
            stem = party.name if party else conv.name
            file_name = (settings["file_name"] or "").strip() or suggest_file_name(
                stem, prefix="Статистика")
            if not file_name.lower().endswith(".png"):
                file_name += ".png"

            saved = await self.file_picker.save_file(
                dialog_title="Экспорт статистики в PNG",
                file_name=file_name,
                allowed_extensions=["png"],
                src_bytes=data,
            )
            if saved:
                self.toast("Статистика сохранена.")

        self.page.show_dialog(dialogs.stats_export_dialog(
            party.name if party else conv.name,
            party is not None,
            lambda settings: self.page.run_task(confirm, settings),
            lambda _e: self.close_dialog(),
        ))

    def export_support_png(self) -> None:
        """Выгружает расстановку поддержки одной партии на карту."""
        rows = []
        for party in self.parties:
            marks = self.service.support_marks(party.id)
            rows.append((party.id, party.name, party.color,
                         sum(n for _n, n, _c in marks),
                         sum(c for _n, _p, c in marks),
                         len(marks)))
        if not any(row[3] for row in rows):
            self.toast("Ни у одной партии нет очков поддержки.", error=True)
            return

        background = support_map_image(self.service.path.parent)

        async def confirm(settings: dict) -> None:
            self.close_dialog()
            party = self.service.project.party(settings["party_id"])
            if party is None:
                return
            data = render_support_png(
                self.service.support_marks(party.id),
                party.name, party.color, width=settings["width"],
                background=background,
                emblem=self.service.party_emblem_path(party.id),
            )

            file_name = (settings["file_name"] or "").strip() or suggest_file_name(
                party.name, prefix="Поддержка")
            if not file_name.lower().endswith(".png"):
                file_name += ".png"

            saved = await self.file_picker.save_file(
                dialog_title="Экспорт поддержки в PNG",
                file_name=file_name,
                allowed_extensions=["png"],
                src_bytes=data,
            )
            if saved:
                self.toast("Поддержка сохранена.")

        self.page.show_dialog(dialogs.support_export_dialog(
            rows,
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
            extra=self._emblem_block(party) if party else None,
        ))

    def _emblem_block(self, party: Party) -> list[ft.Control]:
        """Строка с эмблемой партии внутри диалога правки.

        Только у заведённой партии: файл эмблемы называется по её
        идентификатору (см. `service.set_party_emblem`), а у новой партии его
        ещё нет — сначала «Сохранить», потом эмблема.
        """
        preview = ft.Container(
            width=theme.fs(44), height=theme.fs(44),
            bgcolor=theme.NEUTRAL_100, border_radius=theme.RADIUS,
            border=ft.Border.all(1, theme.DIVIDER),
            alignment=ft.Alignment.CENTER,
        )
        note = ft.Text("", size=theme.fs(12), color=theme.NEUTRAL_700, expand=True)
        clear_button = theme.ghost_button(
            "Убрать", lambda _e: self._clear_party_emblem(party.id, show), danger=True)

        def show() -> None:
            path = self.service.party_emblem_path(party.id)
            if path is None:
                preview.content = ft.Icon(ft.Icons.IMAGE_OUTLINED, size=theme.fs(18),
                                          color=theme.NEUTRAL_600)
                note.value = "Эмблемы нет — в выгрузке будет цветной квадратик."
            else:
                # Ключ со временем правки: без него Flet оставил бы на экране
                # прошлую картинку — путь-то не изменился.
                preview.content = ft.Image(src=f"{path}?v={path.stat().st_mtime_ns}",
                                           fit=ft.BoxFit.CONTAIN,
                                           width=theme.fs(40), height=theme.fs(40))
                note.value = "Показывается в углу выгрузки поддержки."
            clear_button.visible = path is not None
            for control in (preview, note, clear_button):
                push(control)

        show()
        return [
            ft.Container(height=1, bgcolor=theme.DIVIDER),
            ft.Row([
                preview,
                ft.Column([
                    ft.Text("Эмблема", size=theme.fs(13), color=theme.TEXT),
                    note,
                ], spacing=2, tight=True, expand=True),
                theme.secondary_button("Выбрать…",
                                       lambda _e: self._pick_party_emblem(party.id, show)),
                clear_button,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ]

    def _pick_party_emblem(self, party_id: str, done) -> None:
        """Выбирает картинку под эмблему и кладёт её рядом с проектом."""

        async def pick() -> None:
            files = await self.file_picker.pick_files(
                dialog_title="Эмблема партии",
                allowed_extensions=[s.lstrip(".") for s in EMBLEM_SUFFIXES],
                allow_multiple=False,
                with_data=True,
            )
            if not files:
                return
            picked = files[0]
            data = read_picked_bytes(picked)
            suffix = Path(getattr(picked, "name", "") or
                          getattr(picked, "path", "") or "").suffix
            if not is_image(data):
                self.toast("Это не картинка — выберите PNG, JPG или WEBP.", error=True)
                return
            try:
                self.service.set_party_emblem(party_id, data, suffix)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            done()
            self.render()

        self.page.run_task(pick)

    def _clear_party_emblem(self, party_id: str, done) -> None:
        try:
            self.service.clear_party_emblem(party_id)
        except (ValidationError, StoreError) as error:
            self.toast(str(error), error=True)
            return
        done()
        self.render()

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

    # -- коалиции -------------------------------------------------------------

    def new_coalition(self) -> None:
        self._open_coalition_dialog(None)

    def edit_coalition(self, coalition_id: str) -> None:
        conv = self.selected
        found = next((c for c in conv.coalitions if c.id == coalition_id), None)
        if found is not None:
            self._open_coalition_dialog(found)

    def _open_coalition_dialog(self, coalition) -> None:
        if not self.is_editable:
            return
        conv = self.selected
        if len(self.parties) < model.MIN_COALITION:
            self.toast(f"Чтобы собрать блок, нужно хотя бы "
                       f"{fmt.pluralize(model.MIN_COALITION, fmt.PARTIES_COUNT)}.",
                       error=True)
            return

        # Партии, уже занятые другим блоком: в диалоге их галочки закрыты, и
        # видно, кем именно заняты.
        taken = {pid: other.name for other in conv.coalitions
                 if coalition is None or other.id != coalition.id
                 for pid in other.members}

        def save(name: str, color: str, members: list[str]) -> str | None:
            try:
                if coalition:
                    self.service.update_coalition(conv.id, coalition.id, name,
                                                  color, members)
                else:
                    self.service.create_coalition(conv.id, name, color, members)
            except (ValidationError, StoreError) as error:
                return str(error)

            self.close_dialog()
            self.render()
            self.toast(f"Коалиция «{name.strip()}» "
                       f"{'обновлена' if coalition else 'собрана'}.")
            return None

        self.page.show_dialog(dialogs.coalition_dialog(
            coalition, self.parties, conv.seats, taken,
            len(self.parties) + len(conv.coalitions),
            save, lambda _e: self.close_dialog()))

    def delete_coalition(self, coalition_id: str) -> None:
        """Распускает блок. Мест это не меняет: они принадлежат партиям."""
        if not self.is_editable:
            return
        conv = self.selected
        found = next((c for c in conv.coalitions if c.id == coalition_id), None)
        if found is None:
            return

        def confirm(_event) -> None:
            try:
                self.service.delete_coalition(conv.id, coalition_id)
            except (ValidationError, StoreError) as error:
                self.toast(str(error), error=True)
                return
            self.close_dialog()
            self.render()
            self.toast(f"Коалиция «{found.name}» распущена.")

        self.page.show_dialog(dialogs._shell(
            "Распустить коалицию",
            [ft.Text(f"Распустить блок «{found.name}»?\n\n"
                     "Партии останутся на своих местах — блок был только "
                     "договорённостью, а мандаты принадлежат им.",
                     size=theme.fs(14), color=theme.TEXT)],
            [theme.secondary_button("Отмена", lambda _e: self.close_dialog()),
             theme.primary_button("Распустить", confirm, danger=True)],
        ))

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
        blocs = self.blocs(conv)
        if not blocs:
            self.toast("Нечего экспортировать: места не распределены.", error=True)
            return
        legend = [
            LegendEntry(
                bloc.name, bloc.color, bloc.seats,
                film=bloc.film,
                parts=tuple((m.name, m.color, m.seats) for m in bloc.members)
                if bloc.is_coalition else (),
                votes=bloc.votes,
                part_votes=tuple(m.votes for m in bloc.members)
                if bloc.is_coalition else (),
            )
            for bloc in blocs
        ]

        async def confirm(settings: dict) -> None:
            self.close_dialog()
            data = render_png(
                legend,
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
            [(m.name, m.color, m.seats) for bloc in blocs for m in bloc.members],
            lambda settings: self.page.run_task(confirm, settings),
            lambda _e: self.close_dialog(),
            chart_dist=coalitions.chart_distribution(blocs),
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
