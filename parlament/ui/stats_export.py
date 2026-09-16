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

from ..statistics import Battleground, IslandRow
from . import format as fmt
from . import theme
from .export import _font

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Сколько строк в каждом списке «где бороться» — как и на экране.
_SHORTLIST = 4
#: Сколько партий подписывать под полоской острова в общем виде.
_TOP_PARTIES = 3

_FACTOR_NAMES = {"national": "настроение по стране", "island": "сдвиг по острову",
                 "wobble": "колебание"}


def render_stats_png(convocation_name: str, parties, islands: list[IslandRow],
                     attribution: dict, party_id: str | None = None,
                     summary: tuple | None = None,
                     ground: list[Battleground] | None = None,
                     width: int = 1920, emblem: Path | None = None) -> bytes:
    """Собирает картинку статистики.

    :param parties: `(id, название, сокращение, цвет)` — справочник партий.
    :param islands: строки по островам (см. `statistics.island_rows`).
    :param attribution: `{party_id: {слагаемое: ±мандаты}}`.
    :param party_id: чей это взгляд; `None` — общая статистика.
    :param summary: `(мандаты, вся палата, доля голосов)` для партийного вида.
    :param ground: округа глазами партии, для блока «где бороться».
    :param emblem: эмблема партии — в шапку партийного вида.
    """
    plan = _Plan(width, party_id is not None, bool(ground))
    canvas = Image.new("RGB", (width, plan.height), theme.BG)
    draw = ImageDraw.Draw(canvas)
    pen = _Pen(canvas, draw, width)

    by_id = {pid: (name, abbr, color) for pid, name, abbr, color in parties}
    pen.y = plan.pad

    if party_id is None:
        pen.title("Статистика выборов", convocation_name)
    else:
        name, _abbr, color = by_id.get(party_id, ("Партия", "", theme.NEUTRAL_600))
        pen.party_head(name, color, summary, emblem)

    pen.section("По островам",
                "Где за партию голосуют" if party_id
                else "Кто где силён и сколько людей стоит за мандатом")
    pen.islands(islands, by_id, party_id)

    if ground:
        pen.section("Где бороться", "Что даст больше всего при том же усилии")
        pen.battlegrounds(ground)

    pen.section("Что решило результат",
                "Пересчёт того же дележа мест без одного слагаемого")
    pen.attribution(attribution, parties, party_id)

    buffer = io.BytesIO()
    canvas.crop((0, 0, width, min(plan.height, pen.y + plan.pad))).save(buffer, format="PNG")
    return buffer.getvalue()


class _Plan:
    """Сколько места занимает картинка — считается до отрисовки."""

    def __init__(self, width: int, per_party: bool, with_ground: bool):
        self.pad = round(width * 0.025)
        head = round(width * 0.075) if per_party else round(width * 0.055)
        islands = round(width * 0.135)
        battles = round(width * 0.155) if with_ground else 0
        attribution = round(width * 0.09)
        section = round(width * 0.05)
        self.height = (self.pad * 2 + head + section * (2 + int(with_ground))
                       + islands + battles + attribution)


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

    def islands(self, rows: list[IslandRow], by_id: dict, party_id: str | None) -> None:
        if not rows:
            return
        gap = round(self.width * 0.014)
        column = (self.width - self.pad * 2 - gap * (len(rows) - 1)) // len(rows)
        title = round(self.width * 0.0115)
        small = round(self.width * 0.0095)

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

            bar = round(self.width * 0.006)
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
                top = sorted(row.shares.items(), key=lambda kv: -kv[1])[:_TOP_PARTIES]
                spot = x
                for pid, share in top:
                    color = by_id.get(pid, ("", "?", theme.EMPTY_SEAT))[2]
                    abbr = by_id.get(pid, ("", "?", ""))[1] or "?"
                    box = round(small * 0.75)
                    self.draw.rectangle([spot, y + box * 0.2, spot + box, y + box * 1.2],
                                        fill=color)
                    text = f"{abbr} {share:.0f} %"
                    self.draw.text((spot + box * 1.5, y), text,
                                   font=_font(_REGULAR, small), fill=theme.TEXT, anchor="la")
                    spot += box * 1.5 + self.draw.textlength(
                        text, font=_font(_REGULAR, small)) + round(self.width * 0.008)
            else:
                share = row.shares.get(party_id, 0.0)
                self.draw.text((x, y), f"{fmt.share(share)} голосов",
                               font=_font(_SEMIBOLD, small), fill=theme.TEXT, anchor="la")
            y += round(small * 1.9)
            self.draw.text((x, y), f"{fmt.count(row.people_per_seat)} чел. на мандат",
                           font=_font(_REGULAR, small), fill=theme.NEUTRAL_700, anchor="la")

        self.y += round(self.width * 0.085)

    def battlegrounds(self, ground: list[Battleground]) -> None:
        near = sorted((b for b in ground if b.won == 0 and b.share > 0),
                      key=lambda b: b.gap)[:_SHORTLIST]
        missed = sorted((b for b in ground if b.missed_threshold),
                        key=lambda b: -b.share)[:_SHORTLIST]
        free = sorted((b for b in ground if b.free > 0),
                      key=lambda b: (-b.free, -b.seats))[:_SHORTLIST]

        columns = [
            ("Почти взяли", "Округ достался другим, но отрыв небольшой",
             [(b.name, "−" + f"{b.gap:.1f}".replace(".", ",") + " п.п.") for b in near],
             "Все округа с долей уже взяты"),
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
            self.draw.text((x, y), note, font=_font(_REGULAR, small),
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
            self.draw.text((self.pad + box * 1.8, y), name, font=_font(_REGULAR, small),
                           fill=theme.TEXT, anchor="la")
            spot = self.pad + round(self.width * 0.16)
            for key, value in moved.items():
                shift = f"+{value}" if value > 0 else f"−{abs(value)}"
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
