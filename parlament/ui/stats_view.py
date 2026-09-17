"""Экран статистики выборов: два взгляда на один и тот же разбор.

«Общая» — расклад для ведущего: кто на каком острове силён и что сдвинуло
результат. «По партии» — тот же разбор глазами одного игрока: где за него
голосуют и где есть за что бороться.

Считает не этот модуль, а `parlament.statistics` через сервис: здесь только
раскладка. Поэтому одни и те же числа попадают и сюда, и в выгрузку PNG
(`stats_export`), и разойтись им не с чего.
"""

from __future__ import annotations

import flet as ft

from ..statistics import Battleground, GroupRow
from . import dialogs, format as fmt, theme
from .mount import push

#: Сколько строк показывать в каждом списке «где бороться». Больше — уже не
#: подсказка, а вторая карта округов: выбирать всё равно из первых.
_SHORTLIST = 4

#: Сколько партий подписывать под полоской острова в общем виде. Дальше идут
#: доли, на которых остров не держится, а строка перестаёт читаться.
_TOP_PARTIES = 3


class StatsView:
    """Экран «Статистика»."""

    def __init__(self, app):
        self.app = app
        self.service = app.service
        #: Тело под шапкой экрана — перерисовывается при смене вида и партии,
        #: чтобы не пересобирать окно целиком на каждое переключение.
        self.body = ft.Container(expand=True)

    # -- сборка -------------------------------------------------------------

    def build(self) -> ft.Control:
        conv = self.app.selected
        if not conv.has_election:
            return self._no_election()
        if not self.app.parties:
            return self._notice("Сначала создайте партии")

        self._fill()
        return ft.Container(
            expand=True,
            padding=ft.Padding.only(left=28, right=28, top=18, bottom=18),
            content=ft.Column([self._switcher(), self.body], spacing=16, expand=True),
        )

    def _switcher(self) -> ft.Control:
        """Переключатель вида и выбор партии — всё в одну строку."""
        app = self.app
        mode = app.stats_mode
        row: list[ft.Control] = [
            theme.label("Статистика"),
            ft.Container(width=14),
            _tab("Общая", mode == "general", lambda _e: self._choose("general", None)),
            _tab("По партии", mode == "party",
                 lambda _e: self._choose("party", app.stats_party or app.parties[0].id)),
            _tab("Округа", mode == "districts",
                 lambda _e: self._choose("districts", app.stats_party)),
        ]
        # Партию выбирают и для партийного вида, и для таблицы округов: в
        # таблице от неё зависят колонки «наши мандаты» и «до мандата».
        if mode in ("party", "districts"):
            row.append(ft.Container(width=18))
            if mode == "districts":
                row.append(_plain_chip("Все партии", app.stats_party is None,
                                       lambda _e: self._choose("districts", None)))
            for party in app.parties:
                row.append(_chip(party, party.id == app.stats_party,
                                 lambda _e, p=party: self._choose(mode, p.id)))
        return ft.Row(row, spacing=6, wrap=True, run_spacing=6,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _choose(self, mode: str, party_id: str | None) -> None:
        self.app.stats_mode = mode
        self.app.stats_party = party_id
        self.app.render()

    def _sort_by(self, key: str) -> None:
        """Щелчок по заголовку столбца: тот же столбец — обратный порядок."""
        app = self.app
        app.stats_sort = (key, not app.stats_sort[1]) if app.stats_sort[0] == key \
            else (key, key in ("name", "island", "to_next"))
        app.render()

    def _fill(self) -> None:
        conv = self.app.selected
        if self.app.stats_mode == "districts":
            self.body.content = self._districts(self.app.stats_party)
        elif self.app.stats_party is None:
            self.body.content = self._general(self.service.island_stats(conv.id))
        else:
            self.body.content = self._for_party(self.app.stats_party,
                                                self.service.island_stats(conv.id))
        push(self.body)

    # -- общий вид ----------------------------------------------------------

    def _general(self, islands: list[GroupRow]) -> ft.Control:
        return ft.Column([
            _section("По островам", "Кто где силён и сколько людей стоит за мандатом"),
            ft.Row(self._group_cards(islands, None), spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            _section("Город и село",
                     "Второй срез той же карты: в городах 68 мандатов из 120, "
                     "и расклад там обычно совсем не сельский"),
            ft.Row(self._group_cards(self.service.settlement_type_stats(
                self.app.selected.id), None, wide=True),
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            self._attribution_block(),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

    def _group_cards(self, rows: list[GroupRow], party_id: str | None,
                     wide: bool = False) -> list[ft.Control]:
        """Карточка на каждый кусок карты — остров или город/село.

        Партии подписаны все, а не тройка сильнейших: за тройкой пряталась
        четверть расклада, и по карточке нельзя было понять, есть ли тут
        вообще твоя партия.
        """
        colors = {p.id: p.color for p in self.app.parties}
        abbr = {p.id: (p.abbr or p.name[:2]) for p in self.app.parties}
        party = self.service.project.party(party_id) if party_id else None

        cards: list[ft.Control] = []
        for row in rows:
            if party is None:
                bar = dialogs.seat_bar(
                    sorted(((colors.get(pid, theme.EMPTY_SEAT), seats)
                            for pid, seats in row.by_party.items() if seats),
                           key=lambda pair: -pair[1]), height=7)
                note = (f"{fmt.pluralize(row.seats, fmt.MANDATES)} · "
                        f"{fmt.people(row.population)} чел.")
                legend = ft.Row([
                    ft.Row([theme.swatch(colors.get(pid, theme.EMPTY_SEAT), 8),
                            ft.Text(f"{abbr.get(pid, '?')} {share:.0f} %",
                                    size=theme.fs(11), color=theme.TEXT)],
                           spacing=4, tight=True)
                    for pid, share in sorted(row.shares.items(), key=lambda kv: -kv[1])
                    if share > 0
                ], spacing=10, wrap=True, run_spacing=4)
            else:
                share = row.shares.get(party_id, 0.0)
                bar = _share_bar(share, party.color)
                note = (f"{row.by_party.get(party_id, 0)} из "
                        f"{fmt.pluralize(row.seats, fmt.MANDATES_OF)}")
                legend = ft.Text(f"{fmt.share(share)} голосов", size=theme.fs(13),
                                 font_family=theme.FONT_SEMIBOLD, color=theme.TEXT)

            cards.append(_card([
                _card_title(row.name),
                _muted(note),
                ft.Container(bar, padding=ft.Padding.symmetric(vertical=8)),
                legend,
                ft.Container(height=4),
                _muted(f"{fmt.count(row.people_per_seat)} чел. на мандат"),
            ], grow=not wide))
        if wide:
            cards.append(ft.Container(expand=True))
        return cards

    # -- вид одной партии ---------------------------------------------------

    def _for_party(self, party_id: str, islands: list[GroupRow]) -> ft.Control:
        party = self.service.project.party(party_id)
        if party is None:
            return self._notice("Партия не найдена")

        conv = self.app.selected
        seats = conv.seats.get(party_id, 0)
        total = self.app.total_seats
        votes = self.service.vote_shares(conv.id).get(party_id, 0.0)
        ground = self.service.battlegrounds(conv.id, party_id)

        return ft.Column([
            self._party_summary(party, seats, total, votes),
            _section("По островам", "Где за партию голосуют"),
            ft.Row(self._group_cards(islands, party_id), spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            _section("Город и село", "Тот же расклад, но по типу округа"),
            ft.Row(self._group_cards(
                self.service.settlement_type_stats(conv.id), party_id, wide=True),
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            _section("Где бороться",
                     "Что даст больше всего при том же усилии. Прибавка "
                     "показана в процентных пунктах (п.п.) — это доля голосов "
                     "округа, а не проценты от неё"),
            self._battle_block(ground),
            self._attribution_block(party_id),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

    def _party_summary(self, party, seats: int, total: int, votes: float) -> ft.Control:
        """Строка итога: мандаты, голоса и расхождение между ними."""
        seat_share = seats / total * 100 if total else 0.0
        gap = seat_share - votes
        # Расхождение мест с голосами — то самое, ради чего эти числа стоят
        # рядом: округа делятся по большинству, и доля палаты почти никогда
        # не совпадает с долей голосов.
        note = ("мандатов больше, чем голосов" if gap > 0.05
                else "мандатов меньше, чем голосов" if gap < -0.05
                else "мандаты и голоса сошлись")
        return _card([
            ft.Row([
                theme.swatch(party.color, 14),
                ft.Text(party.name, size=theme.fs(18),
                        font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
            ], spacing=8),
            ft.Container(height=8),
            ft.Row([
                _figure(str(seats), fmt.plural(seats, fmt.MANDATES)),
                _figure(fmt.share(seat_share), "палаты"),
                _figure(fmt.share(votes), "голосов"),
                _figure(_signed(gap) + " п.п.", note,
                        tooltip="Процентные пункты — разница между долей "
                                "мандатов и долей голосов. Не проценты: "
                                "с 10 % до 11 % — это +1 п.п., но +10 % роста."),
            ], spacing=28),
        ], grow=False)

    def _battle_block(self, ground: list[Battleground]) -> ft.Control:
        """Три списка: где почти взяли, где не хватило барьера, где пусто."""
        # Сортируем по тому, сколько не хватает до мандата, а не по отрыву
        # от победителя: округ многомандатный, обгонять лидера незачем.
        near = sorted((b for b in ground if b.to_next is not None),
                      key=lambda b: b.to_next)[:_SHORTLIST]
        missed = sorted((b for b in ground if b.missed_threshold),
                        key=lambda b: -b.share)[:_SHORTLIST]
        free = sorted((b for b in ground if b.free > 0),
                      key=lambda b: (-b.free, -b.seats))[:_SHORTLIST]

        return ft.Row([
            _list_card(
                "Ближе всего к мандату",
                "Насколько надо подрасти, чтобы взять здесь ещё одно место",
                [(b.name, "+" + f"{b.to_next:.1f}".replace(".", ",") + " п.п.")
                 for b in near],
                "Брать больше нечего: все мандаты уже наши",
            ),
            _list_card(
                "Не хватило до барьера",
                "Доля есть, но меньше 5 % — мандатов не дают",
                [(b.name, fmt.share(b.share)) for b in missed],
                "Везде, где партия набрала долю, барьер пройден",
            ),
            _list_card(
                "Свободные очки",
                "Сюда не дошёл никто — дешёвая поддержка",
                [(b.name, f"{b.free} из {b.capacity}") for b in free],
                "Весь запас очков на карте уже разобран",
            ),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START)

    # -- таблица округов ----------------------------------------------------

    #: Столбцы таблицы: ключ, заголовок, ширина. Ширина None — столбец тянется.
    _COLUMNS = (
        ("name", "Округ", None),
        ("island", "Остров", 150),
        ("seats", "Мандатов", 85),
        ("won", "Наши", 60),
        ("share", "Наша доля", 90),
        ("leader", "Победитель", 120),
        ("to_next", "До мандата, п.п.", 120),
        ("points", "Очки", 80),
    )

    def _districts(self, party_id: str | None) -> ft.Control:
        """Все округа одной таблицей — уровень, которого не было совсем.

        Короткие списки «где бороться» отвечают, куда смотреть в первую
        очередь; здесь — всё целиком, с сортировкой по любому столбцу.
        """
        conv = self.app.selected
        party = self.service.project.party(party_id) if party_id else None
        rows = self.service.battlegrounds(conv.id, party_id or self.app.parties[0].id)
        by_id = {p.id: p for p in self.app.parties}
        shares = {b.district_id: self.service.district_shares(conv.id, b.district_id)
                  for b in rows}

        key, downwards = self.app.stats_sort
        rows = sorted(rows, key=lambda b: _sort_key(b, key, party_id),
                      reverse=downwards)

        header = ft.Row([
            _head_cell(title, width, key == column, downwards,
                       lambda _e, c=column: self._sort_by(c))
            for column, title, width in self._COLUMNS
            if party is not None or column not in ("won", "share", "to_next")
        ], spacing=8)

        body = []
        for index, b in enumerate(rows):
            leader = by_id.get(b.leader)
            cells: list[ft.Control] = [
                _cell(b.name, None, bold=True),
                _cell(_short_island(b.island), 150),
                _cell(str(b.seats), 85),
            ]
            if party is not None:
                cells += [
                    _cell(str(b.won), 60,
                          color=theme.TEXT if b.won else theme.NEUTRAL_600),
                    _cell(fmt.share(b.share), 90),
                ]
            cells.append(ft.Container(
                width=120,
                content=ft.Row([
                    theme.swatch(leader.color, 8) if leader else ft.Container(width=8),
                    ft.Text(leader.abbr or leader.name if leader else "—",
                            size=theme.fs(12), color=theme.TEXT, no_wrap=True),
                    ft.Text(f"{shares[b.district_id].get(b.leader, 0):.0f} %"
                            if leader else "",
                            size=theme.fs(11), color=theme.NEUTRAL_600),
                ], spacing=4, tight=True)))
            if party is not None:
                cells.append(_cell(
                    "+" + f"{b.to_next:.1f}".replace(".", ",") if b.to_next is not None
                    else "весь наш", 100,
                    color=theme.ACCENT_700 if b.to_next is not None else theme.NEUTRAL_600))
            cells.append(_cell(f"{b.points}/{b.capacity}" if party is not None
                               else f"{b.capacity - b.free}/{b.capacity}", 80,
                               color=theme.NEUTRAL_600 if not b.free else theme.TEXT))

            body.append(ft.Container(
                bgcolor=theme.SURFACE if index % 2 else ft.Colors.TRANSPARENT,
                padding=ft.Padding.symmetric(horizontal=8, vertical=5),
                content=ft.Row(cells, spacing=8),
            ))

        note = ("Столбцы «Наши», «Наша доля» и «До мандата» появляются, когда "
                "выбрана партия" if party is None
                else "«До мандата» — на сколько надо подрасти, чтобы взять "
                     "здесь ещё одно место")
        return ft.Column([
            _section(f"Округа: {fmt.pluralize(len(rows), fmt.DISTRICTS)}", note),
            ft.Container(
                bgcolor=theme.SURFACE,
                padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                border=ft.Border.only(bottom=ft.BorderSide(1, theme.DIVIDER)),
                content=header),
            ft.Column(body, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
        ], spacing=10, expand=True)

    # -- что решило результат -----------------------------------------------

    def _attribution_block(self, only: str | None = None) -> ft.Control:
        """Сколько мандатов дало или отняло каждое слагаемое.

        Показываем только тех, у кого хоть что-то сдвинулось: строка из трёх
        нулей ничего не сообщает, а таких строк обычно большинство.
        """
        table = self.service.attribution(self.app.selected.id)
        names = {"national": "настроение по стране", "island": "сдвиг по острову",
                 "wobble": "колебание"}

        rows = []
        for party in self.app.parties:
            if only is not None and party.id != only:
                continue
            moved = {k: v for k, v in (table.get(party.id) or {}).items() if v}
            if not moved:
                continue
            rows.append(ft.Row([
                theme.swatch(party.color, 10),
                ft.Container(ft.Text(party.name, size=theme.fs(13), color=theme.TEXT,
                                     no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                             width=210),
                ft.Row([_delta(value, names[key]) for key, value in moved.items()],
                       spacing=18, wrap=True, run_spacing=4),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        if not rows:
            body = [_muted("Ни поправки, ни колебание не сдвинули "
                           "ни одного мандата — расклад целиком из очков поддержки.")]
        else:
            body = rows

        return ft.Column([
            _section("Что решило результат",
                     "Плюс — сколько мандатов слагаемое принесло, минус — "
                     "сколько отняло. Считается пересчётом того же дележа "
                     "мест с занулённым слагаемым."),
            _card(body, grow=False),
        ], spacing=14, tight=True)

    # -- заглушки -----------------------------------------------------------

    def _no_election(self) -> ft.Control:
        return self._notice(
            "Статистика появится после выборов",
            "К выборам", self.app.show_elections,
            "Считать нечего: у состава, набранного руками, нет ни долей "
            "голосов, ни разбора по округам.")

    def _notice(self, title: str, button: str | None = None,
                action=None, note: str = "") -> ft.Control:
        body: list[ft.Control] = [
            ft.Text(title, size=theme.fs(18), font_family=theme.FONT_SEMIBOLD,
                    color=theme.TEXT),
        ]
        if note:
            body.append(ft.Text(note, size=theme.fs(13), color=theme.NEUTRAL_700,
                                text_align=ft.TextAlign.CENTER))
        if button and action:
            body.append(ft.Container(
                theme.primary_button(button, lambda _e: action()),
                padding=ft.Padding.only(top=6)))
        return ft.Container(
            expand=True, alignment=ft.Alignment.CENTER,
            content=ft.Column(body, spacing=8, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )


# -- мелкие кирпичики -------------------------------------------------------


def _section(title: str, note: str) -> ft.Control:
    return ft.Column([
        theme.label(title),
        ft.Text(note, size=theme.fs(12), color=theme.NEUTRAL_700),
    ], spacing=2, tight=True)


def _card(body: list[ft.Control], grow: bool = True) -> ft.Control:
    return ft.Container(
        expand=grow,
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.DIVIDER),
        border_radius=theme.RADIUS,
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        content=ft.Column(body, spacing=0, tight=True),
    )


def _card_title(text: str) -> ft.Control:
    return ft.Text(text, size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                   color=theme.TEXT, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                   tooltip=text)


def _muted(text: str) -> ft.Control:
    return ft.Text(text, size=theme.fs(11), color=theme.NEUTRAL_700)


def _figure(value: str, note: str, tooltip: str | None = None) -> ft.Control:
    """Крупное число с подписью под ним."""
    return ft.Column([
        ft.Text(value, size=theme.fs(22), font_family=theme.FONT_SEMIBOLD,
                color=theme.TEXT),
        ft.Text(note, size=theme.fs(11), color=theme.NEUTRAL_700),
    ], spacing=0, tight=True, tooltip=tooltip)


def _signed(value: float) -> str:
    """«+0,7» или «−1,2» — с типографским минусом, как везде в интерфейсе."""
    text = f"{abs(value):.1f}".replace(".", ",")
    return ("+" if value >= 0 else "\u2212") + text


def _delta(value: int, note: str) -> ft.Control:
    """«+7 мандатов» зелёным или «−9 мандатов» красным — вклад слагаемого.

    Единицу пишем прямо здесь: одно «−9» рядом с названием слагаемого
    читалось ребусом — непонятно, мандаты это, проценты или очки.
    """
    color = theme.ACCENT_700 if value > 0 else theme.ACCENT_2_700
    sign = "+" if value > 0 else "\u2212"
    return ft.Row([
        ft.Text(f"{sign}{fmt.pluralize(abs(value), fmt.MANDATES)}",
                size=theme.fs(14), font_family=theme.FONT_SEMIBOLD, color=color),
        ft.Text(note, size=theme.fs(11), color=theme.NEUTRAL_700),
    ], spacing=5, tight=True)


def _share_bar(share: float, color: str, height: int = 7) -> ft.Control:
    """Доля партии на острове: её цвет и серый остаток."""
    mine = max(1, round(share * 10))
    return ft.Row([
        ft.Container(bgcolor=color, expand=mine),
        ft.Container(bgcolor=theme.NEUTRAL_300, expand=max(1, 1000 - mine)),
    ], spacing=1, height=height)


def _list_card(title: str, note: str, rows: list[tuple[str, str]],
               empty: str) -> ft.Control:
    body: list[ft.Control] = [
        ft.Text(title, size=theme.fs(13), font_family=theme.FONT_SEMIBOLD,
                color=theme.TEXT),
        _muted(note),
        ft.Container(height=8),
    ]
    if not rows:
        body.append(_muted(empty))
    for name, value in rows:
        body.append(ft.Row([
            ft.Container(ft.Text(name, size=theme.fs(12), color=theme.TEXT,
                                 no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                                 tooltip=name), expand=True),
            ft.Text(value, size=theme.fs(12), font_family=theme.FONT_SEMIBOLD,
                    color=theme.NEUTRAL_900),
        ], spacing=8))
    return _card(body)


def _short_island(name: str) -> str:
    """«Остров Вакула (запад)» -> «Вакула»: в таблице важен столбец, не титул."""
    text = name.replace("Остров ", "")
    return text.split(" (")[0]


def _sort_key(row, key: str, party_id: str | None):
    """Ключ сортировки таблицы. Пустое «до мандата» всегда в конце."""
    if key == "to_next":
        return (row.to_next is None, row.to_next or 0.0)
    if key == "leader":
        return (row.leader or "",)
    if key == "points":
        return (row.points if party_id else row.capacity - row.free,)
    if key == "island":
        return (row.island, row.name)
    return (getattr(row, key, 0),)


def _head_cell(title: str, width: int | None, active: bool, downwards: bool,
               on_click) -> ft.Control:
    """Заголовок столбца — он же кнопка сортировки."""
    mark = ("  ↓" if downwards else "  ↑") if active else ""
    return ft.Container(
        width=width, expand=width is None, on_click=on_click, ink=True,
        content=ft.Text(title + mark, size=theme.fs(11),
                        font_family=theme.FONT_SEMIBOLD if active else theme.FONT_FAMILY,
                        color=theme.ACCENT_700 if active else theme.NEUTRAL_700,
                        no_wrap=True),
    )


def _cell(text: str, width: int | None, bold: bool = False,
          color: str | None = None) -> ft.Control:
    return ft.Container(
        width=width, expand=width is None,
        content=ft.Text(text, size=theme.fs(12), color=color or theme.TEXT,
                        font_family=theme.FONT_SEMIBOLD if bold else theme.FONT_FAMILY,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, tooltip=text),
    )


def _plain_chip(text: str, active: bool, on_click) -> ft.Control:
    """Кнопка выбора без цвета партии — «Все партии» в таблице округов."""
    return ft.Container(
        on_click=on_click, ink=True,
        padding=ft.Padding.symmetric(horizontal=9, vertical=5),
        bgcolor=theme.SURFACE if active else ft.Colors.TRANSPARENT,
        border=ft.Border.all(1, theme.ACCENT if active else theme.DIVIDER),
        border_radius=theme.RADIUS,
        content=ft.Text(text, size=theme.fs(12),
                        color=theme.TEXT if active else theme.NEUTRAL_700),
    )


def _tab(text: str, active: bool, on_click) -> ft.Control:
    """Вкладка переключателя вида — подчёркнутая, когда выбрана."""
    return ft.Container(
        on_click=on_click, ink=True,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        bgcolor=theme.SURFACE if active else ft.Colors.TRANSPARENT,
        border=ft.Border.all(1, theme.ACCENT if active else theme.DIVIDER),
        border_radius=theme.RADIUS,
        content=ft.Text(text, size=theme.fs(13),
                        font_family=theme.FONT_SEMIBOLD if active else theme.FONT_FAMILY,
                        color=theme.ACCENT_700 if active else theme.NEUTRAL_700),
    )


def _chip(party, active: bool, on_click) -> ft.Control:
    """Кнопка выбора партии — цветной квадратик и название."""
    return ft.Container(
        on_click=on_click, ink=True, tooltip=party.name,
        padding=ft.Padding.symmetric(horizontal=8, vertical=5),
        bgcolor=theme.SURFACE if active else ft.Colors.TRANSPARENT,
        border=ft.Border.all(1, theme.ACCENT if active else theme.DIVIDER),
        border_radius=theme.RADIUS,
        content=ft.Row([
            theme.swatch(party.color, 9),
            ft.Text(party.abbr or party.name, size=theme.fs(12),
                    color=theme.TEXT if active else theme.NEUTRAL_700),
        ], spacing=5, tight=True),
    )
