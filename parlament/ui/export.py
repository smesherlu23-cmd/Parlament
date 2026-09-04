"""Экспорт схемы в PNG.

Картинку рисует Pillow, а не Flet: нужен точный размер файла (1920×1080 и выше
по ТЗ) независимо от размера окна, а Canvas отдаёт только то, что видно на
экране. Геометрия мест берётся из `seat_chart.compute_seats`, поэтому картинка
совпадает с тем, что показано в окне.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from ..model import DEFAULT_TOTAL_SEATS
from . import theme
from .seat_chart import VIEWBOX_HEIGHT, VIEWBOX_WIDTH, compute_seats

#: Разрешения из диалога экспорта.
RESOLUTIONS = [
    ("1920 × 1080", 1920, 1080),
    ("2560 × 1440", 2560, 1440),
    ("3840 × 2160", 3840, 2160),
]


@dataclass
class LegendEntry:
    name: str
    color: str
    seats: int


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(theme.FONTS_DIR / name), size)


def render_png(
    distribution: list[LegendEntry],
    total_seats: int = DEFAULT_TOTAL_SEATS,
    rows: int = 5,
    width: int = 1920,
    height: int = 1080,
    title: str = "",
    with_legend: bool = True,
    with_title: bool = False,
) -> bytes:
    """Собирает картинку и отдаёт её содержимым PNG-файла."""
    image = Image.new("RGB", (width, height), theme.BG)
    draw = ImageDraw.Draw(image)

    # Всё построено от высоты: композиция одинакова на 1080p и на 4K.
    unit = height / 1080
    margin = round(72 * unit)
    cursor_y = margin

    if with_title and title:
        title_font = _font("SourceSerif4-SemiBold.ttf", round(52 * unit))
        draw.text((width / 2, cursor_y), title, font=title_font,
                  fill=theme.TEXT, anchor="ma")
        cursor_y += round(78 * unit)

    legend_lines = _layout_legend(distribution, total_seats, width - margin * 2, unit) \
        if with_legend else []
    line_height = round(44 * unit)
    legend_height = len(legend_lines) * line_height

    # Схема занимает всё, что осталось между заголовком и легендой, но не шире
    # полосы полей — иначе на широком кадре полукруг растянулся бы во всю ширину.
    available_height = height - cursor_y - margin - legend_height
    available_width = width - margin * 2
    chart_width = min(available_width, available_height * VIEWBOX_WIDTH / VIEWBOX_HEIGHT)
    chart_height = chart_width * VIEWBOX_HEIGHT / VIEWBOX_WIDTH

    _draw_chart(
        draw,
        origin_x=(width - chart_width) / 2,
        origin_y=cursor_y + (available_height - chart_height) / 2,
        chart_width=chart_width,
        total_seats=total_seats,
        rows=rows,
        distribution=distribution,
    )

    if legend_lines:
        _draw_legend(draw, legend_lines, margin, height - margin - legend_height,
                     available_width, unit, line_height)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_chart(draw: ImageDraw.ImageDraw, origin_x: float, origin_y: float,
                chart_width: float, total_seats: int, rows: int,
                distribution: list[LegendEntry]) -> None:
    scale = chart_width / VIEWBOX_WIDTH
    dist = [(entry.color, entry.seats) for entry in distribution]

    for seat in compute_seats(total_seats, rows, dist):
        x = origin_x + seat.x * scale
        y = origin_y + seat.y * scale
        r = seat.radius * scale
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=seat.color,
            outline=theme.BG,
            width=max(1, round(1.2 * scale)),
        )


def _layout_legend(distribution: list[LegendEntry], total_seats: int,
                   max_width: float, unit: float) -> list[list[dict]]:
    """Раскладывает легенду по строкам — как в окне, с переносом."""
    if not distribution:
        return []

    font_size = round(26 * unit)
    swatch_size = round(20 * unit)
    gap = round(12 * unit)
    column_gap = round(48 * unit)
    font = _font("SourceSerif4-Regular.ttf", font_size)

    items = []
    for entry in distribution:
        label = f"{entry.name} {entry.seats}"
        percent = f" ({entry.seats / total_seats * 100:.1f}".replace(".", ",") + " %)"
        items.append({
            "entry": entry,
            "label": label,
            "percent": percent,
            "label_width": draw_text_length(font, label),
            "percent_width": draw_text_length(font, percent),
            "width": swatch_size + gap + draw_text_length(font, label + percent),
            "font": font,
            "swatch": swatch_size,
            "gap": gap,
            "column_gap": column_gap,
        })

    lines: list[list[dict]] = [[]]
    line_width = 0.0
    for item in items:
        needed = (column_gap if lines[-1] else 0) + item["width"]
        if lines[-1] and line_width + needed > max_width:
            lines.append([item])
            line_width = item["width"]
        else:
            lines[-1].append(item)
            line_width += needed
    return lines


def draw_text_length(font: ImageFont.FreeTypeFont, text: str) -> float:
    return font.getlength(text)


def _draw_legend(draw: ImageDraw.ImageDraw, lines: list[list[dict]],
                 x: float, y: float, width: float, unit: float,
                 line_height: int) -> None:
    for line_index, line in enumerate(lines):
        line_width = sum(
            item["width"] + (item["column_gap"] if index else 0)
            for index, item in enumerate(line)
        )
        cursor_x = x + (width - line_width) / 2      # строки легенды по центру
        center_y = y + line_index * line_height + line_height / 2

        for item in line:
            size = item["swatch"]
            draw.rectangle(
                [cursor_x, center_y - size / 2, cursor_x + size, center_y + size / 2],
                fill=item["entry"].color,
            )
            cursor_x += size + item["gap"]

            draw.text((cursor_x, center_y), item["label"], font=item["font"],
                      fill=theme.TEXT, anchor="lm")
            cursor_x += item["label_width"]

            draw.text((cursor_x, center_y), item["percent"], font=item["font"],
                      fill=theme.NEUTRAL_700, anchor="lm")
            cursor_x += item["percent_width"] + item["column_gap"]


def suggest_file_name(convocation_name: str, prefix: str = "Парламент") -> str:
    """«Парламент_Третий_состав.png» — с оглядкой на запрещённые в Windows символы.

    Созыв переименовывается свободно, а `\\ / : * ? " < > |` в имени файла
    Windows не принимает: без чистки диалог сохранения споткнулся бы на
    названии вида «Созыв 3/4».
    """
    safe = convocation_name
    for bad in '\\/:*?"<>|':
        safe = safe.replace(bad, "")
    stem = "_".join(safe.split())
    return f"{prefix}_{stem}.png" if stem else f"{prefix}.png"
