"""Выгрузка статистики выборов в PNG — те же блоки, что и на экране.

Числа сюда приходят готовыми (`parlament.statistics` через сервис), здесь
только раскладка в картинку: разойтись с экраном им не с чего, а считать
дважды незачем.

Высота картинки не задаётся, а набирается: блоков в общем и партийном виде
разное число, и заранее подобранная высота либо резала бы низ, либо
оставляла пустую полосу. Поэтому сначала считаем, сколько нужно, и только
потом рисуем.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from ..statistics import Battleground, GroupRow
from . import format as fmt
from . import theme
from .export import _font

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Сколько строк в каждом списке «где бороться» — как и на экране.
_SHORTLIST = 4
_FACTOR_NAMES = {"national": "настроение по стране", "island": "сдвиг по острову",
                 "wobble": "колебание"}


def render_stats_png(convocation_name: str, parties, islands: list[GroupRow],
                     attribution: dict, party_id: str | None = None,
                     summary: tuple | None = None,
                     ground: list[Battleground] | None = None,
                     country: GroupRow | None = None,
                     table: list[Battleground] | None = None,
                     width: int = 1920, emblem: Path | None = None) -> bytes:
    """Собирает картинку статистики.

    :param parties: `(id, название, сокращение, цвет)` — справочник партий.
    :param islands: строки по островам (см. `statistics.island_rows`).
    :param attribution: `{party_id: {слагаемое: ±мандаты}}`.
    :param party_id: чей это взгляд; `None` — общая статистика.
    :param summary: `(мандаты, вся палата, доля голосов)` для партийного вида.
    :param ground: округа глазами партии, для блока «где бороться».
    :param country: вся Конфедерация одной строкой — только в общем виде,
                    в партийном те же числа уже есть в шапке (`summary`).
    :param table: если задано — рисуется только таблица округов, отдельным
                  файлом: 27 строк поверх обзора сделали бы картинку, в
                  которой не найти ни того, ни другого. Личных колонок в
                  ней нет — `party_id` на неё не влияет.
    :param emblem: эмблема партии — в шапку партийного вида.
    """
    by_id = {pid: (name, fmt.short_name(name, abbr), color)
             for pid, name, abbr, color in parties}

    if table is not None:
        return _render_table(convocation_name, table, by_id, width, emblem)

    plan = _Plan(width, party_id is not None, bool(ground), bool(country))
    canvas = Image.new("RGB", (width, plan.height), theme.BG)
    draw = ImageDraw.Draw(canvas)
    pen = _Pen(canvas, draw, width)
    pen.y = plan.pad

    if party_id is None:
        pen.title("Статистика выборов", convocation_name)
    else:
        name, _abbr, color = by_id.get(party_id, ("Партия", "", theme.NEUTRAL_600))
        pen.party_head(name, color, summary, emblem)

    if country is not None:
        pen.section("По стране", "Вся Конфедерация одним взглядом — точка "
                                 "отсчёта: на её фоне видно, где остров "
                                 "держит перевес, а где идёт вровень со "
                                 "средним")
        pen.islands([country], by_id, None, hero=True)

    pen.section("По островам",
                "Где за партию голосуют" if party_id
                else "Кто где силён и сколько людей стоит за мандатом")
    pen.islands(islands, by_id, party_id)

    if ground:
        pen.section("Где бороться",
                    "Что даст больше всего при том же усилии. Прибавка "
                    "показана в процентных пунктах (п.п.) — это доля голосов "
                    "округа, а не проценты от неё")
        pen.battlegrounds(ground)

    pen.section("Что решило результат",
                "Плюс — сколько мандатов слагаемое принесло, минус — сколько "
                "отняло. Считается пересчётом того же дележа мест с "
                "занулённым слагаемым.")
    pen.attribution(attribution, parties, party_id)

    buffer = io.BytesIO()
    canvas.crop((0, 0, width, min(plan.height, pen.y + plan.pad))).save(buffer, format="PNG")
    return buffer.getvalue()


def _render_table(convocation_name: str, rows: list[Battleground], by_id: dict,
                  width: int, emblem: Path | None) -> bytes:
    """Таблица округов целиком — отдельная картинка.

    Личных колонок нет вовсе: эту картинку рассылают всем разом, а не одному
    игроку, так что "наших" мандатов или доли здесь не бывает — только то,
    что видно одинаково для всех: округ, остров, число мандатов и победитель.
    """
    pad = round(width * 0.025)
    line = round(width * 0.021)
    head = round(width * 0.075)
    canvas = Image.new("RGB", (width, head + pad + line * (len(rows) + 2)), theme.BG)
    draw = ImageDraw.Draw(canvas)
    pen = _Pen(canvas, draw, width)
    pen.y = pad

    pen.title("Округа", f"{convocation_name} · {fmt.pluralize(len(rows), fmt.DISTRICTS)}")

    small = round(width * 0.0098)
    leader_spot = 0.62
    columns = [
        ("Округ", 0.0, "l"),
        ("Остров", 0.32, "l"),
        ("Мандатов", 0.54, "r"),
        ("Победитель", leader_spot, "l"),
    ]

    y = pen.y + round(small * 0.6)
    for title, spot, align in columns:
        draw.text((pad + (width - pad * 2) * spot, y), title,
                  font=_font(_SEMIBOLD, small), fill=theme.NEUTRAL_700,
                  anchor="la" if align == "l" else "ra")
    y += round(small * 1.9)
    draw.line([pad, y - small * 0.5, width - pad, y - small * 0.5],
              fill=theme.DIVIDER, width=1)

    for index, row in enumerate(rows):
        if index % 2:
            draw.rectangle([pad // 2, y - small * 0.35, width - pad // 2,
                            y + small * 1.45], fill=theme.SURFACE)
        leader = by_id.get(row.leader) if row.leader else None
        values = [
            (_clip(draw, row.name, _font(_SEMIBOLD, small), (width - pad * 2) * 0.28),
             0.0, "l", theme.TEXT, _SEMIBOLD),
            (_short_island(row.island), 0.32, "l", theme.NEUTRAL_700, _REGULAR),
            (str(row.seats), 0.54, "r", theme.TEXT, _REGULAR),
            (f"{leader[1]} {row.leader_share:.0f} %" if leader else "—",
             leader_spot, "l", theme.TEXT, _REGULAR),
        ]

        for text, spot, align, color, font_name in values:
            draw.text((pad + (width - pad * 2) * spot, y), text,
                      font=_font(font_name, small), fill=color,
                      anchor="la" if align == "l" else "ra")
        if leader is not None:
            box = round(small * 0.7)
            draw.rectangle([pad + (width - pad * 2) * leader_spot - box * 1.6,
                            y + box * 0.2,
                            pad + (width - pad * 2) * leader_spot - box * 0.6,
                            y + box * 1.2], fill=leader[2])
        y += line

    buffer = io.BytesIO()
    canvas.crop((0, 0, width, min(canvas.height, y + pad))).save(buffer, format="PNG")
    return buffer.getvalue()


def _short_island(name: str) -> str:
    """«Остров Вакула (запад)» -> «Вакула»: в таблице важен столбец, не титул."""
    return name.replace("Остров ", "").split(" (")[0]


class _Plan:
    """Сколько места занимает картинка — считается до отрисовки."""

    def __init__(self, width: int, per_party: bool, with_ground: bool,
                 with_country: bool = False):
        self.pad = round(width * 0.025)
        head = round(width * 0.075) if per_party else round(width * 0.055)
        islands = round(width * 0.155)
        # Карточка страны крупнее (см. `hero` в `_Pen.islands`) — тот же
        # множитель 1.3, что и у шрифта там, иначе бюджет высоты выйдет
        # меньше настоящей карточки и низ картинки молча обрежется.
        country = round(width * 0.155 * 1.3) if with_country else 0
        battles = round(width * 0.155) if with_ground else 0
        attribution = round(width * 0.09)
        section = round(width * 0.05)
        blocks = 2 + int(with_ground) + int(with_country)
        self.height = (self.pad * 2 + head + section * blocks
                       + islands + country + battles + attribution)


class _Pen:
    """Рисует блоки сверху вниз, запоминая, где остановился."""

    def __init__(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, width: int):
        self.canvas = canvas
        self.draw = draw
        self.width = width
        self.pad = round(width * 0.025)
        self.y = 0

    # -- шапка --------------------------------------------------------------

    def title(self, text: str, note: str) -> None:
        size = round(self.width * 0.024)
        self.draw.text((self.pad, self.y), text, font=_font(_SEMIBOLD, size),
                       fill=theme.TEXT, anchor="la")
        self.draw.text((self.pad, self.y + size * 1.35), note,
                       font=_font(_REGULAR, round(size * 0.62)),
                       fill=theme.NEUTRAL_700, anchor="la")
        self.y += round(size * 2.4)

    def party_head(self, name: str, color: str, summary, emblem: Path | None) -> None:
        size = round(self.width * 0.024)
        left = self.pad
        badge = round(self.width * 0.05)

        mark = _load(emblem)
        if mark is not None:
            mark = _fit(mark, badge)
            self.canvas.paste(mark, (left, self.y), mark)
            left += mark.width + round(self.width * 0.012)
        else:
            box = round(size * 0.85)
            self.draw.rectangle([left, self.y + size * 0.15,
                                 left + box, self.y + size * 0.15 + box], fill=color)
            left += box + round(self.width * 0.010)

        self.draw.text((left, self.y), name, font=_font(_SEMIBOLD, size),
                       fill=theme.TEXT, anchor="la")
        if summary:
            seats, total, votes = summary
            seat_share = seats / total * 100 if total else 0.0
            line = (f"{fmt.pluralize(seats, fmt.MANDATES)}  ·  "
                    f"{fmt.share(seat_share)} палаты  ·  "
                    f"{fmt.share(votes)} голосов  ·  "
                    f"{_signed(seat_share - votes)} п.п.")
            self.draw.text((left, self.y + size * 1.35), line,
                           font=_font(_REGULAR, round(size * 0.62)),
                           fill=theme.NEUTRAL_700, anchor="la")
        self.y += max(badge, round(size * 2.4))

    def section(self, title: str, note: str) -> None:
        size = round(self.width * 0.013)
        self.y += round(self.width * 0.012)
        self.draw.line([self.pad, self.y, self.width - self.pad, self.y],
                       fill=theme.DIVIDER, width=1)
        self.y += round(size * 0.9)
        self.draw.text((self.pad, self.y), title.upper(),
                       font=_font(_SEMIBOLD, size), fill=theme.TEXT, anchor="la")
        self.draw.text((self.pad, self.y + size * 1.3), note,
                       font=_font(_REGULAR, round(size * 0.85)),
                       fill=theme.NEUTRAL_700, anchor="la")
        self.y += round(size * 2.7)

    # -- блоки --------------------------------------------------------------

    def islands(self, rows: list[GroupRow], by_id: dict, party_id: str | None,
               hero: bool = False) -> None:
        """Карточка на каждую группу — остров или, при `hero`, страна целиком.

        `hero` — крупнее шрифт и полоска: у страны в общем виде всегда одна
        такая карточка на весь ряд, и она должна читаться как заголовок,
        а не как ещё один остров.
        """
        if not rows:
            return
        scale = 1.3 if hero else 1.0
        gap = round(self.width * 0.014)
        column = (self.width - self.pad * 2 - gap * (len(rows) - 1)) // len(rows)
        title = round(self.width * 0.0115 * scale)
        small = round(self.width * 0.0095 * scale)

        for index, row in enumerate(rows):
            x = self.pad + index * (column + gap)
            y = self.y
            self.draw.text((x, y), row.name, font=_font(_SEMIBOLD, title),
                           fill=theme.TEXT, anchor="la")
            y += round(title * 1.55)

            if party_id is None:
                note = (f"{fmt.pluralize(row.seats, fmt.MANDATES)} · "
                        f"{fmt.people(row.population)} чел.")
            else:
                mine = row.by_party.get(party_id, 0)
                note = f"{mine} из {fmt.pluralize(row.seats, fmt.MANDATES_OF)}"
            self.draw.text((x, y), note, font=_font(_REGULAR, small),
                           fill=theme.NEUTRAL_700, anchor="la")
            y += round(small * 1.8)

            bar = round(self.width * 0.006 * (1.4 if hero else 1.0))
            if party_id is None:
                segments = sorted(((by_id.get(pid, ("", "", theme.EMPTY_SEAT))[2], seats)
                                   for pid, seats in row.by_party.items() if seats),
                                  key=lambda pair: -pair[1])
                _bar(self.draw, x, y, column, bar, segments)
            else:
                share = row.shares.get(party_id, 0.0)
                color = by_id.get(party_id, ("", "", theme.NEUTRAL_600))[2]
                _bar(self.draw, x, y, column, bar,
                     [(color, max(1, round(share * 10))),
                      (theme.NEUTRAL_300, max(1, 1000 - round(share * 10)))])
            y += bar + round(small * 1.1)

            if party_id is None:
                lines = self._legend(x, y, column, small, row.shares, by_id)
                y += round(small * 1.9) * lines
            else:
                share = row.shares.get(party_id, 0.0)
                self.draw.text((x, y), f"{fmt.share(share)} голосов",
                               font=_font(_SEMIBOLD, small), fill=theme.TEXT, anchor="la")
                y += round(small * 1.9)
            self.draw.text((x, y), f"{fmt.count(row.people_per_seat)} чел. на мандат",
                           font=_font(_REGULAR, small), fill=theme.NEUTRAL_700, anchor="la")

        self.y += self._islands_height(rows, party_id, hero)

    def _legend(self, x: int, y: int, column: int, small: int,
                shares: dict, by_id: dict) -> int:
        """Подписи всех партий под полоской; переносит на новую строку.

        Возвращает, сколько строк заняло: в узкой колонке острова шесть
        партий в одну строку не влезают, а раскладка ниже должна знать, на
        сколько её сдвинуть.
        """
        font = _font(_REGULAR, small)
        box = round(small * 0.75)
        gap = round(self.width * 0.006)
        spot, lines = x, 1
        for pid, share in sorted(shares.items(), key=lambda kv: -kv[1]):
            if share <= 0:
                continue
            text = f"{by_id.get(pid, ('', '?', ''))[1]} {share:.0f} %"
            need = box * 1.5 + self.draw.textlength(text, font=font)
            if spot > x and spot + need > x + column:
                spot, lines = x, lines + 1
                y += round(small * 1.9)
            self.draw.rectangle([spot, y + box * 0.2, spot + box, y + box * 1.2],
                                fill=by_id.get(pid, ("", "?", theme.EMPTY_SEAT))[2])
            self.draw.text((spot + box * 1.5, y), text, font=font,
                           fill=theme.TEXT, anchor="la")
            spot += need + gap
        return lines

    def _islands_height(self, rows: list[GroupRow], party_id: str | None,
                        hero: bool = False) -> int:
        """Сколько заняла полоса карточек — с учётом перенесённых подписей."""
        scale = 1.3 if hero else 1.0
        small = round(self.width * 0.0095 * scale)
        base = round(self.width * 0.105 * scale)
        if party_id is not None or not rows:
            return base
        column = ((self.width - self.pad * 2
                   - round(self.width * 0.014) * (len(rows) - 1)) // len(rows))
        font = _font(_REGULAR, small)
        box = round(small * 0.75)
        gap = round(self.width * 0.006)
        most = 1
        for row in rows:
            spot, lines = 0, 1
            for pid, share in sorted(row.shares.items(), key=lambda kv: -kv[1]):
                if share <= 0:
                    continue
                need = box * 1.5 + self.draw.textlength(f"?? {share:.0f} %", font=font)
                if spot > 0 and spot + need > column:
                    spot, lines = 0, lines + 1
                spot += need + gap
            most = max(most, lines)
        return base + round(small * 1.9) * (most - 1)

    def battlegrounds(self, ground: list[Battleground]) -> None:
        near = sorted((b for b in ground if b.to_next is not None),
                      key=lambda b: b.to_next)[:_SHORTLIST]
        missed = sorted((b for b in ground if b.missed_threshold),
                        key=lambda b: -b.share)[:_SHORTLIST]
        free = sorted((b for b in ground if b.free > 0),
                      key=lambda b: (-b.free, -b.seats))[:_SHORTLIST]

        columns = [
            ("Ближе всего к мандату",
             "Насколько надо подрасти, чтобы взять здесь ещё одно место",
             [(b.name, "+" + f"{b.to_next:.1f}".replace(".", ",") + " п.п.")
              for b in near],
             "Брать больше нечего: все мандаты уже наши"),
            ("Не хватило до барьера", "Доля есть, но меньше 5 % — мандатов не дают",
             [(b.name, fmt.share(b.share)) for b in missed],
             "Барьер пройден везде, где есть доля"),
            ("Свободные очки", "Сюда не дошёл никто — дешёвая поддержка",
             [(b.name, f"{b.free} из {b.capacity}") for b in free],
             "Весь запас очков разобран"),
        ]

        gap = round(self.width * 0.014)
        column = (self.width - self.pad * 2 - gap * 2) // 3
        title = round(self.width * 0.0115)
        small = round(self.width * 0.0095)

        for index, (head, note, rows, empty) in enumerate(columns):
            x = self.pad + index * (column + gap)
            y = self.y
            self.draw.text((x, y), head, font=_font(_SEMIBOLD, title),
                           fill=theme.TEXT, anchor="la")
            y += round(title * 1.5)
            self.draw.text((x, y), _clip(self.draw, note, _font(_REGULAR, small),
                                         column * 0.95),
                           font=_font(_REGULAR, small),
                           fill=theme.NEUTRAL_700, anchor="la")
            y += round(small * 2.1)
            if not rows:
                self.draw.text((x, y), empty, font=_font(_REGULAR, small),
                               fill=theme.NEUTRAL_600, anchor="la")
            for name, value in rows:
                self.draw.text((x, y), _clip(self.draw, name, _font(_REGULAR, small),
                                             column * 0.62),
                               font=_font(_REGULAR, small), fill=theme.TEXT, anchor="la")
                self.draw.text((x + column, y), value, font=_font(_SEMIBOLD, small),
                               fill=theme.NEUTRAL_900, anchor="ra")
                y += round(small * 1.75)

        self.y += round(self.width * 0.105)

    def attribution(self, table: dict, parties, party_id: str | None) -> None:
        small = round(self.width * 0.0105)
        rows = 0
        for pid, name, _abbr, color in parties:
            if party_id is not None and pid != party_id:
                continue
            moved = {k: v for k, v in (table.get(pid) or {}).items() if v}
            if not moved:
                continue
            y = self.y + rows * round(small * 1.9)
            box = round(small * 0.8)
            self.draw.rectangle([self.pad, y + box * 0.2, self.pad + box, y + box * 1.2],
                                fill=color)
            spot = self.pad + round(self.width * 0.16)
            # Длинное название иначе наезжало бы на цифру сдвига — колонка
            # с мандатами всегда начинается на одном и том же месте.
            label = _clip(self.draw, name, _font(_REGULAR, small),
                         spot - (self.pad + box * 1.8) - small * 0.6)
            self.draw.text((self.pad + box * 1.8, y), label, font=_font(_REGULAR, small),
                           fill=theme.TEXT, anchor="la")
            for key, value in moved.items():
                # Единица прямо у числа: одно «−9» рядом с названием
                # слагаемого читалось ребусом — мандаты это или проценты.
                sign = "+" if value > 0 else "−"
                shift = f"{sign}{fmt.pluralize(abs(value), fmt.MANDATES)}"
                color_ = theme.ACCENT_700 if value > 0 else theme.ACCENT_2_700
                self.draw.text((spot, y), shift, font=_font(_SEMIBOLD, small),
                               fill=color_, anchor="la")
                spot += self.draw.textlength(shift, font=_font(_SEMIBOLD, small)) + small * 0.5
                text = _FACTOR_NAMES.get(key, key)
                self.draw.text((spot, y), text, font=_font(_REGULAR, small),
                               fill=theme.NEUTRAL_700, anchor="la")
                spot += self.draw.textlength(text, font=_font(_REGULAR, small)) + small * 1.6
            rows += 1

        if not rows:
            self.draw.text((self.pad, self.y),
                           "Ни поправки, ни колебание не сдвинули ни одного мандата — "
                           "расклад целиком из очков поддержки.",
                           font=_font(_REGULAR, small), fill=theme.NEUTRAL_700, anchor="la")
            rows = 1
        self.y += rows * round(small * 1.9)


# -- мелочи -----------------------------------------------------------------


def _bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int,
         segments) -> None:
    """Полоска из долей — как `dialogs.seat_bar` в окне."""
    total = sum(max(1, value) for _color, value in segments) or 1
    spot = x
    for color, value in segments:
        piece = width * max(1, value) / total
        draw.rectangle([spot, y, spot + piece, y + height], fill=color)
        spot += piece


def _clip(draw: ImageDraw.ImageDraw, text: str, font, limit: float) -> str:
    """Обрезает название округа, если оно не влезает в колонку."""
    if draw.textlength(text, font=font) <= limit:
        return text
    while text and draw.textlength(text + "…", font=font) > limit:
        text = text[:-1]
    return text + "…"


def _signed(value: float) -> str:
    return ("+" if value >= 0 else "−") + f"{abs(value):.1f}".replace(".", ",")


def _fit(image: Image.Image, box: int) -> Image.Image:
    image = image.convert("RGBA")
    scale = box / max(image.width, image.height)
    return image.resize((max(1, round(image.width * scale)),
                         max(1, round(image.height * scale))), Image.LANCZOS)


def _load(path: Path | None) -> Image.Image | None:
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return image.copy()
    except Exception:
        # Эмблему не прочитали — выгрузка всё равно должна собраться.
        return None
