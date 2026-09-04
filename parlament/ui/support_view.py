"""Экран «Поддержка»: населённые пункты округов и очки популярности.

Каждый населённый пункт несёт фиксированный запас очков неформальной
популярности, который игроки делят между партиями. На выборах из них
получается модификатор: сумма очков партии в округе, делённая на число
пунктов.

Населённых пунктов в присланной карте нет, поэтому список наполняется
здесь же — округ раскрывается, и в него добавляются пункты.

Очки правятся сразу, без кнопки «сохранить»: это долгоживущие данные игры,
которые меняются по ходу партии, а не разовый ввод перед выборами.
"""

from __future__ import annotations

import flet as ft

from ..elections import SETTLEMENT_SUPPORT
from ..service import ValidationError
from ..store import StoreError
from . import theme
from .mount import push

_POINT_WIDTH = 64
_NAME_WIDTH = 240


class SupportView:
    """Список округов с раскрывающимися населёнными пунктами."""

    def __init__(self, app):
        self.app = app
        self.service = app.service
        #: Какие округа раскрыты — переживает перерисовку экрана.
        self.opened: set[str] = set(app.support_opened)

    def build(self) -> ft.Control:
        if not self.app.parties:
            return self._notice("Сначала создайте партии", "Новая партия",
                                self.app.show_parties)
        if not self.service.project.districts:
            return self._notice("В проекте нет округов", "К карте", self.app.show_map)

        rows: list[ft.Control] = []
        last_region = None
        for district in self.service.project.districts:
            if district.region != last_region:
                last_region = district.region
                rows.append(ft.Container(
                    padding=ft.Padding.only(top=16, bottom=4),
                    content=theme.label(district.region or "Прочие"),
                ))
            rows.append(self._district(district))

        return ft.Column([
            ft.Container(
                padding=ft.Padding.only(left=28, right=28, top=18, bottom=6),
                content=ft.Text(
                    f"{SETTLEMENT_SUPPORT} очков на населённый пункт · "
                    f"модификатор — очки округа, делённые на число пунктов",
                    size=theme.fs(13), color=theme.NEUTRAL_700,
                ),
            ),
            ft.Container(
                expand=True,
                padding=ft.Padding.only(left=28, right=28, bottom=18),
                # Две прокрутки: вниз по округам и вбок — при десяти партиях
                # строка пункта шире окна, и без этого правые столбцы
                # недостижимы.
                content=ft.Row([
                    ft.Column(rows, spacing=4, scroll=ft.ScrollMode.AUTO,
                              expand=True, width=self._row_width()),
                ], scroll=ft.ScrollMode.AUTO, expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START),
            ),
        ], spacing=0, expand=True)

    def _row_width(self) -> float:
        """Ширина строки пункта — по числу партий, а не по окну.

        Внутри горизонтальной прокрутки колонка иначе сжимается по окну, и
        прокручивать становится нечего.
        """
        return _NAME_WIDTH + (_POINT_WIDTH + 8) * len(self.app.parties) + 68

    # -- округ --------------------------------------------------------------

    def _district(self, district) -> ft.Control:
        opened = district.id in self.opened
        settlements = district.settlements

        head = ft.Container(
            padding=ft.Padding.symmetric(vertical=8, horizontal=10),
            bgcolor=theme.NEUTRAL_100 if opened else None,
            border_radius=theme.RADIUS,
            ink=True,
            on_click=lambda _e, d=district: self._toggle(d.id),
            content=ft.Row([
                ft.Text("▾" if opened else "▸", size=theme.fs(13),
                        color=theme.NEUTRAL_600),
                ft.Container(
                    ft.Text(district.name, size=theme.fs(14), color=theme.TEXT,
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                    expand=True,
                ),
                ft.Text(self._summary(district), size=theme.fs(12),
                        color=theme.NEUTRAL_600),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        if not opened:
            return head

        body: list[ft.Control] = []
        if settlements:
            body.append(self._header())
            for settlement in settlements:
                body.append(self._settlement(district, settlement))
        else:
            body.append(ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                content=ft.Text("Населённых пунктов пока нет.",
                                size=theme.fs(13), color=theme.NEUTRAL_700),
            ))
        body.append(ft.Container(
            padding=ft.Padding.only(top=6),
            content=theme.ghost_button("+ Населённый пункт",
                                       lambda _e, d=district: self._add(d)),
        ))

        return ft.Column([
            head,
            ft.Container(
                margin=ft.Margin.only(left=22, bottom=8),
                padding=ft.Padding.only(left=12, top=4, bottom=8, right=8),
                border=ft.Border.only(left=ft.BorderSide(2, theme.DIVIDER)),
                content=ft.Column(body, spacing=2, tight=True),
            ),
        ], spacing=0, tight=True)

    def _summary(self, district) -> str:
        count = len(district.settlements)
        if not count:
            return "нет пунктов"
        leaders = sorted(
            ((p, district.support_points(p.id)) for p in self.app.parties),
            key=lambda pair: -pair[1],
        )
        top = [f"{p.abbr or p.name} {n}" for p, n in leaders[:2] if n]
        tail = "  ".join(top) if top else "очки не розданы"
        return f"{count} п. · {tail}"

    def _header(self) -> ft.Control:
        return ft.Row([
            ft.Container(theme.label("Пункт"), width=_NAME_WIDTH),
            *[ft.Container(
                ft.Row([
                    theme.swatch(p.color, 10),
                    ft.Container(
                        ft.Text(p.abbr or p.name, size=theme.fs(11),
                                color=theme.NEUTRAL_600, no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        expand=True),
                ], spacing=5),
                width=_POINT_WIDTH, tooltip=p.name)
              for p in self.app.parties],
            ft.Container(theme.label("Всего"), width=60),
        ], spacing=8)

    def _settlement(self, district, settlement) -> ft.Control:
        used = sum(settlement.support.values())
        total = ft.Text(f"{used} / {SETTLEMENT_SUPPORT}", size=theme.fs(12),
                        color=theme.ACCENT_2_700 if used > SETTLEMENT_SUPPORT
                        else theme.NEUTRAL_700)

        fields: list[ft.Control] = []
        for party in self.app.parties:
            value = settlement.support.get(party.id, 0)
            field = theme.text_field(str(value) if value else "",
                                     width=_POINT_WIDTH,
                                     text_align=ft.TextAlign.RIGHT,
                                     keyboard_numeric=True)
            field.data = (district.id, settlement.id, party.id, total)
            field.on_change = self._on_points
            fields.append(field)

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=3),
            content=ft.Row([
                ft.Container(
                    ft.Row([
                        ft.Container(
                            ft.Text(settlement.name, size=theme.fs(13),
                                    color=theme.TEXT, no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            expand=True, ink=True,
                            tooltip="Переименовать",
                            on_click=lambda _e, d=district, s=settlement:
                                self._rename(d, s)),
                        theme.icon_button(
                            ft.Icons.CLOSE,
                            lambda _e, d=district, s=settlement: self._delete(d, s),
                            danger=True, tooltip="Убрать пункт", size=12),
                    ], spacing=2),
                    width=_NAME_WIDTH),
                *fields,
                ft.Container(total, width=60),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    # -- действия -----------------------------------------------------------

    def _toggle(self, district_id: str) -> None:
        if district_id in self.opened:
            self.opened.discard(district_id)
        else:
            self.opened.add(district_id)
        self.app.support_opened = set(self.opened)
        self.app.render()

    def _add(self, district) -> None:
        from . import dialogs

        def confirm(name: str) -> str | None:
            try:
                self.service.add_settlement(district.id, name)
            except (ValidationError, StoreError) as error:
                return str(error)
            self.opened.add(district.id)
            self.app.support_opened = set(self.opened)
            self.app.close_dialog()
            self.app.render()
            return None

        self.app.page.show_dialog(dialogs.settlement_dialog(
            district, confirm, lambda _e: self.app.close_dialog()))

    def _rename(self, district, settlement) -> None:
        from . import dialogs

        def confirm(name: str) -> str | None:
            try:
                self.service.rename_settlement(district.id, settlement.id, name)
            except (ValidationError, StoreError) as error:
                return str(error)
            self.app.close_dialog()
            self.app.render()
            return None

        self.app.page.show_dialog(dialogs.settlement_dialog(
            district, confirm, lambda _e: self.app.close_dialog(),
            settlement=settlement))

    def _delete(self, district, settlement) -> None:
        try:
            self.service.delete_settlement(district.id, settlement.id)
        except (ValidationError, StoreError) as error:
            self.app.toast(str(error), error=True)
            return
        self.app.render()

    def _on_points(self, event) -> None:
        """Пишет очки сразу: это данные игры, а не разовый ввод."""
        field = event.control
        district_id, settlement_id, party_id, total = field.data

        cleaned = "".join(ch for ch in (field.value or "") if ch.isdigit())
        if cleaned != field.value:
            field.value = cleaned
            push(field)

        try:
            self.service.set_support(district_id, settlement_id, party_id,
                                     int(cleaned or 0))
        except ValidationError as error:
            # Возвращаем прежнее значение: иначе на экране осталось бы число,
            # которого нет в проекте.
            settlement = self.service.project.district(district_id).settlement(settlement_id)
            previous = settlement.support.get(party_id, 0)
            field.value = str(previous) if previous else ""
            push(field)
            self.app.toast(str(error), error=True)
        except StoreError as error:
            self.app.toast(str(error), error=True)

        settlement = self.service.project.district(district_id).settlement(settlement_id)
        used = sum(settlement.support.values())
        total.value = f"{used} / {SETTLEMENT_SUPPORT}"
        total.color = theme.NEUTRAL_700
        push(total)

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
