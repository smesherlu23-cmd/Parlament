"""Отрисовка карты в PNG — тем же Pillow, что и схема зала.

Экран рисует карту на холсте Flet, а выгрузка — здесь: снять картинку с
холста средствами Flet нельзя, поэтому изображение собирается заново. Обе
отрисовки берут одни и те же доли координат из округов, так что расходиться
им не с чего.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from . import theme
from .export import _font  # общий подбор шрифта: тот же Source Serif, что в окне
from .map_chart import FALLBACK_ASPECT, map_image_path

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Диаметр маркера при ширине карты 1920 px — крупнее экранного, чтобы
#: подписи оставались читаемыми при просмотре картинки целиком.
_MARKER_RADIUS = 26

#: Обводка маркера. В окне тут полупрозрачный чёрный «#55000000», но Pillow
#: читает восьмизначный hex как RGBA (альфа последней), а Flet — как ARGB
#: (альфа первой), да и холст здесь без альфа-канала. Поэтому берём готовый
#: серый той же светлоты, а не переносим строку цвета как есть.
_MARKER_OUTLINE = "#8a8686"


def render_map_png(markers, width: int = 1920, title: str | None = None,
                   legend: list[tuple[str, str, int, int]] | None = None) -> bytes:
    """Собирает картинку карты.

    :param markers: `(название, мест, x, y, цвет|None)` по каждому округу.
    :param legend: `(название партии, цвет, округов, мест)` — строки сводки.
    """
    background = _load_background()
    aspect = (background.width / background.height) if background else FALLBACK_ASPECT
    map_height = round(width / aspect)

    title_height = round(width * 0.045) if title else 0
    legend_height = _legend_height(width, legend) if legend else 0
    canvas = Image.new("RGB", (width, title_height + map_height + legend_height),
                       theme.BG)
    draw = ImageDraw.Draw(canvas)

    if title:
        font = _font(_SEMIBOLD, round(width * 0.024))
        draw.text((round(width * 0.03), title_height // 2), title,
                  font=font, fill=theme.TEXT, anchor="lm")

    if background:
        canvas.paste(background.resize((width, map_height), Image.LANCZOS),
                     (0, title_height))
    else:
        draw.rectangle([0, title_height, width, title_height + map_height],
                       fill=theme.NEUTRAL_200)

    radius = max(10, round(_MARKER_RADIUS * width / 1920))
    label_font = _font(_SEMIBOLD, round(radius * 0.95))
    for name, seats, x, y, color in markers:
        cx = round(x * width)
        cy = title_height + round(y * map_height)
        fill = color or theme.EMPTY_SEAT
        draw.ellipse([cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2],
                     fill="#ffffff")
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                     fill=fill, outline=_MARKER_OUTLINE, width=1)
        draw.text((cx, cy), str(seats), font=label_font,
                  fill=_readable_on(fill), anchor="mm")

    if legend:
        _draw_legend(draw, width, title_height + map_height, legend)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _load_background() -> Image.Image | None:
    path = map_image_path()
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except Exception:
        # Подложку не прочитали — карта выгрузится без неё, с одними маркерами.
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
                       fill=color, outline=_MARKER_OUTLINE)
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
