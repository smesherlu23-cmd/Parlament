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

from ..district_geometry import DISTRICT_CENTRES, DISTRICT_SHAPES, MAP_ASPECT
from . import theme
from .export import _font  # общий подбор шрифта: тот же Source Serif, что в окне

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Граница между округами. В окне это белая линия поверх заливки; здесь так же,
#: но цвет задан отдельной константой — перенести строку из Flet как есть
#: нельзя, Pillow читает восьмизначный hex как RGBA, а Flet как ARGB.
_BORDER = "#ffffff"


def render_map_png(districts, width: int = 1920, title: str | None = None,
                   legend: list[tuple[str, str, int, int]] | None = None,
                   background: Path | None = None) -> bytes:
    """Собирает картинку карты.

    :param districts: `(code, название, мест, цвет|None)` по каждому округу.
    :param legend: `(название партии, цвет, округов, мест)` — строки сводки.
    :param background: необязательная подложка под границами.
    """
    map_height = round(width / MAP_ASPECT)
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

    for code, _name, _seats, color in districts:
        fill = color or theme.EMPTY_SEAT
        for poly in DISTRICT_SHAPES.get(code, ()):
            points = [(x * width, title_height + y * map_height) for x, y in poly]
            draw.polygon(points, fill=fill, outline=_BORDER)

    label_font = _font(_SEMIBOLD, max(11, round(width / 95)))
    for code, _name, seats, color in districts:
        centre = DISTRICT_CENTRES.get(code)
        if centre is None:
            continue
        cx = centre[0] * width
        cy = title_height + centre[1] * map_height
        draw.text((cx, cy), str(seats), font=label_font,
                  fill=_readable_on(color or theme.EMPTY_SEAT), anchor="mm")

    if legend:
        _draw_legend(draw, width, title_height + map_height, legend)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


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


def _readable_on(background: str) -> str:
    try:
        r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return theme.TEXT
    return theme.TEXT if (r * 299 + g * 587 + b * 114) / 1000 > 150 else "#ffffff"
