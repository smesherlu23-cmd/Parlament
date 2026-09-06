"""Отрисовка карты в PNG — тем же Pillow, что и схема зала.

Экран рисует карту на холсте Flet, а выгрузка — здесь: снять изображение с
холста средствами Flet нельзя, поэтому картинка собирается заново. Обе
отрисовки берут одни и те же полигоны из `district_geometry`, так что
расходиться им не с чего.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from ..district_geometry import DISTRICT_SHAPES
from . import theme
from .export import _font  # общий подбор шрифта: тот же Source Serif, что в окне
from .map_frame import (
    CONTENT_ASPECT,
    NAME_MARGIN,
    label_spot,
    room_at,
    place,
    text_color,
)

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Граница между округами. В окне это белая линия поверх заливки; здесь так же,
#: но цвет задан отдельной константой — перенести строку из Flet как есть
#: нельзя, Pillow читает восьмизначный hex как RGBA, а Flet как ARGB.
_BORDER = "#ffffff"

#: Во сколько раз карта рисуется крупнее нужного, чтобы потом ужаться.
#: Pillow не сглаживает многоугольники, и береговая линия выходила ступеньками;
#: уменьшение с усреднением даёт ровно тот же эффект, что и сглаживание.
_SUPERSAMPLE = 3


def render_map_png(districts, width: int = 1920, title: str | None = None,
                   legend: list[tuple[str, str, int, int]] | None = None,
                   background: Path | None = None) -> bytes:
    """Собирает картинку карты.

    :param districts: `(code, название, мест, цвет|None)` по каждому округу.
    :param legend: `(название партии, цвет, округов, мест)` — строки сводки.
    :param background: необязательная подложка под границами.
    """
    map_height = round(width / CONTENT_ASPECT)
    title_height = round(width * 0.045) if title else 0
    legend_height = _legend_height(width, legend) if legend else 0

    canvas = Image.new("RGB", (width, title_height + map_height + legend_height),
                       theme.BG)
    draw = ImageDraw.Draw(canvas)

    if title:
        draw.text((round(width * 0.03), title_height // 2), title,
                  font=_font(_SEMIBOLD, round(width * 0.024)),
                  fill=theme.TEXT, anchor="lm")

    under = _load_background(background)
    if under is not None:
        canvas.paste(under.resize((width, map_height), Image.LANCZOS), (0, title_height))
    else:
        # Море: без него острова висят на том же цвете, что и поля вокруг.
        draw.rectangle([0, title_height, width, title_height + map_height], fill=theme.MAP_SEA)

    islands = _render_map(districts, width, map_height)
    # Вставляем по альфе: без маски прозрачное море стало бы чёрным.
    canvas.paste(islands, (0, title_height), islands)

    if legend:
        _draw_legend(draw, width, title_height + map_height, legend)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _render_map(districts, width: int, height: int) -> Image.Image:
    """Рисует сами округа — крупнее нужного и с уменьшением в конце.

    Многоугольники Pillow не сглаживает: береговая линия при обычной
    отрисовке выходит ступеньками, и на 27 островах это бросается в глаза.
    Рисуем втрое крупнее и ужимаем с усреднением — тот же результат, что
    дало бы сглаживание, и без сторонних библиотек.

    Подписи наносим уже после уменьшения: буквы Pillow и так сглаживает, а
    ужатый текст вышел бы мылом.
    """
    big = (width * _SUPERSAMPLE, height * _SUPERSAMPLE)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    stroke = max(1, round(1.6 * width / 1600 * _SUPERSAMPLE))

    for code, _name, _seats, color in districts:
        fill = color or theme.EMPTY_SEAT
        for poly in DISTRICT_SHAPES.get(code, ()):
            points = [place(x, y, 0, 0, big[0], big[1]) for x, y in poly]
            pen.polygon(points, fill=fill, outline=_BORDER, width=stroke)

    small = layer.resize((width, height), Image.LANCZOS)
    _draw_labels(ImageDraw.Draw(small), districts, width, height)
    return small


def _draw_labels(draw: ImageDraw.ImageDraw, districts, width: int,
                 height: int) -> None:
    """Подписывает округа поверх заливки.

    Точки подписей лежат в самой геометрии и гарантированно попадают внутрь
    своего округа. В крупный округ идёт название и число мандатов, в мелкий —
    только номер: городские округа метрополий лежат вплотную, и название там
    налезло бы на соседей. Цвет буквы выбирается по яркости заливки, поэтому
    подпись читается на любой партийной краске.
    """
    base = max(9, round(width * 0.0112))
    # Названия у округов разной длины, а места под них — разное. Пробуем
    # несколько кеглей от крупного к мелкому: подпись целиком лучше номера,
    # пока её вообще можно прочесть.
    steps = [base, round(base * 0.88), round(base * 0.78), round(base * 0.7)]
    fonts = [_font(_SEMIBOLD, size) for size in dict.fromkeys(steps) if size >= 8]
    code_font = _font(_SEMIBOLD, max(8, round(width * 0.0102)))

    for code, name, seats, color in districts:
        spot = label_spot(code, 0, 0, width, height)
        if spot is None:
            continue
        x, y, room = spot
        room *= NAME_MARGIN
        ink = text_color(color or theme.EMPTY_SEAT, theme.TEXT, "#ffffff")

        # Название стоит выше найденной точки, а округ там уже другой
        # ширины — меряем ровно ту строку, куда ляжет текст.
        chosen = name_row = None
        for font in fonts:
            row = y - font.size * 0.6
            here = room_at(code, x, row, 0, 0, width, height) * NAME_MARGIN
            if font.getlength(name) <= min(room, here):
                chosen, name_row = font, row
                break

        if chosen is not None:
            seats_font = _font(_REGULAR, max(7, round(chosen.size * 0.82)))
            draw.text((x, name_row), name, font=chosen, fill=ink, anchor="mm")
            draw.text((x, y + chosen.size * 0.72), str(seats), font=seats_font,
                      fill=ink, anchor="mm")
        elif code_font.getlength(str(code)) <= room:
            # Название не влезло даже мелким — остаётся номер: он и так
            # подписан на игровой карте, а полное имя видно по клику.
            draw.text((x, y), str(code), font=code_font, fill=ink, anchor="mm")


def _load_background(path: Path | None) -> Image.Image | None:
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except Exception:
        # Подложку не прочитали — карта выгрузится по границам округов.
        return None


def _legend_height(width: int, legend) -> int:
    line = round(width * 0.026)
    return round(width * 0.02) * 2 + line * len(legend)


def _draw_legend(draw: ImageDraw.ImageDraw, width: int, top: int, legend) -> None:
    pad = round(width * 0.03)
    line = round(width * 0.026)
    font = _font(_REGULAR, round(width * 0.014))
    y = top + round(width * 0.02)
    box = round(line * 0.42)

    for name, color, districts, seats in legend:
        draw.rectangle([pad, y + (line - box) // 2, pad + box, y + (line + box) // 2],
                       fill=color, outline="#8a8686")
        draw.text((pad + box + round(width * 0.008), y + line // 2), name,
                  font=font, fill=theme.TEXT, anchor="lm")
        draw.text((width - pad, y + line // 2),
                  f"{districts} окр.   {seats} мест",
                  font=font, fill=theme.NEUTRAL_700, anchor="rm")
        y += line
