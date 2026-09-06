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
from .seat_chart import (
    FILM_ALPHA,
    FILM_EDGE_ALPHA,
    VIEWBOX_HEIGHT,
    VIEWBOX_WIDTH,
    compute_seats,
    film_sectors,
    sector_polygon,
)

#: На столько полосы рядов заходят друг на друга, в единицах макета схемы:
#: иначе между ними остаётся щель в пиксель от округления координат.
_FILM_OVERLAP = 1.0

#: Разрешения из диалога экспорта.
RESOLUTIONS = [
    ("1920 × 1080", 1920, 1080),
    ("2560 × 1440", 2560, 1440),
    ("3840 × 2160", 3840, 2160),
]


@dataclass
class LegendEntry:
    """Блок зала: партия сама по себе или коалиция.

    У коалиции заполнены `film` — цвет плёнки — и `parts`: партии, из которых
    она собрана, как «имя, цвет, места». Схема разворачивает блок в места
    участников (каждый своим цветом, все под общей плёнкой), а легенда рисует
    квадратик так же, как выглядит блок на схеме, — полосками под плёнкой.
    """

    name: str
    color: str
    seats: int
    film: str | None = None
    parts: tuple[tuple[str, str, int], ...] = ()
    #: Доля голосов по стране, в процентах. `None` — выборов не было, и
    #: голосов не существует: писать вместо них ноль значило бы соврать.
    votes: float | None = None
    #: Голоса участников блока, в том же порядке, что и `parts`.
    part_votes: tuple[float | None, ...] = ()

    def chart_parts(self) -> list[tuple[str, int, str | None]]:
        """Места блока для схемы: цвет партии, сколько мест, чем накрыты."""
        if not self.parts:
            return [(self.color, self.seats, None)]
        return [(color, seats, self.film) for _name, color, seats in self.parts]

    def legend_rows(self) -> list["LegendEntry"]:
        """Строки легенды: сам блок, а за ним — из кого он собран.

        Участников показываем отдельно, иначе по картинке не понять, чей
        цвет под плёнкой. Проценты у всех считаются от палаты, поэтому в
        сумме строки дают больше сотни — так и должно быть: коалиция и её
        партии занимают одни и те же места, а не разные.
        """
        if not self.parts:
            return [self]
        return [self] + [LegendEntry(name, color, seats, votes=votes)
                         for (name, color, seats), votes in
                         zip(self.parts, self.part_votes or [None] * len(self.parts))]


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
    # Плёнки коалиций полупрозрачны, а на RGB прозрачность рисовать нечем:
    # копим их отдельным слоем и накладываем на готовую картинку.
    film = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    film_draw = ImageDraw.Draw(film)

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
        film_draw,
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

    image = Image.alpha_composite(image.convert("RGBA"), film).convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_chart(draw: ImageDraw.ImageDraw, film_draw: ImageDraw.ImageDraw,
                origin_x: float, origin_y: float,
                chart_width: float, total_seats: int, rows: int,
                distribution: list[LegendEntry]) -> None:
    scale = chart_width / VIEWBOX_WIDTH
    dist = [part for entry in distribution for part in entry.chart_parts()]
    seats = compute_seats(total_seats, rows, dist)

    for seat in seats:
        x = origin_x + seat.x * scale
        y = origin_y + seat.y * scale
        r = seat.radius * scale
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=seat.color,
            outline=theme.BG,
            width=max(1, round(1.2 * scale)),
        )

    _draw_films(film_draw, film_sectors(seats), origin_x, origin_y, scale)


def _draw_films(film_draw: ImageDraw.ImageDraw, sectors, origin_x: float,
                origin_y: float, scale: float) -> None:
    """Кладёт плёнки коалиций — по одной заливке на блок.

    Полосы рядов сперва собираются в маску и только потом красятся: если
    рисовать их по очереди полупрозрачной краской, в местах, где полосы
    заходят друг на друга, плёнка легла бы дважды и по блоку пошли бы тёмные
    швы. В маске наложение ничего не меняет — там просто «закрашено».
    """
    by_color: dict[str, list] = {}
    for sector in sectors:
        by_color.setdefault(sector.color, []).append(sector)

    for color, group in by_color.items():
        mask = Image.new("L", film_draw.im.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        for sector in group:
            points = [(origin_x + x * scale, origin_y + y * scale)
                      for x, y in sector_polygon(sector, pad=_FILM_OVERLAP)]
            mask_draw.polygon(points, fill=255)
        film_draw.bitmap((0, 0), mask, fill=_rgba(color, FILM_ALPHA))


def _rgba(color: str, alpha: float) -> tuple[int, int, int, int]:
    """«#rrggbb» и прозрачность → кортеж, который понимает Pillow."""
    value = color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16),
            max(0, min(255, round(alpha * 255))))


def _layout_legend(distribution: list[LegendEntry], total_seats: int,
                   max_width: float, unit: float) -> list[list[dict]]:
    """Раскладывает легенду по строкам — как в окне, с переносом."""
    if not distribution:
        return []
    distribution = [row for entry in distribution for row in entry.legend_rows()]

    font_size = round(26 * unit)
    swatch_size = round(20 * unit)
    gap = round(12 * unit)
    column_gap = round(48 * unit)
    font = _font("SourceSerif4-Regular.ttf", font_size)

    items = []
    for entry in distribution:
        label = f"{entry.name} {entry.seats}"
        # Одно число рядом с местами, а не два: доля палаты и доля голосов
        # бок о бок читались как одна цифра с непонятным довеском. Когда
        # выборы были, интереснее голоса — с местами их и сравнивают, а сама
        # доля палаты видна по схеме. Иначе на их месте стоит доля палаты.
        if entry.votes is not None:
            percent = f" ({entry.votes:.1f}".replace(".", ",") + " % голосов)"
        else:
            percent = (f" ({entry.seats / total_seats * 100:.1f}".replace(".", ",")
                       + " % мест)")
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
            _draw_swatch(draw, item["entry"], cursor_x, center_y - size / 2, size)
            cursor_x += size + item["gap"]

            draw.text((cursor_x, center_y), item["label"], font=item["font"],
                      fill=theme.TEXT, anchor="lm")
            cursor_x += item["label_width"]

            draw.text((cursor_x, center_y), item["percent"], font=item["font"],
                      fill=theme.NEUTRAL_700, anchor="lm")
            cursor_x += item["percent_width"] + item["column_gap"]


def _draw_swatch(draw: ImageDraw.ImageDraw, entry: LegendEntry,
                 x: float, y: float, size: float) -> None:
    """Квадратик легенды: у партии — её цвет, у коалиции — сама коалиция.

    Блок показывается так же, как на схеме: полоски участников, накрытые
    плёнкой. Один общий цвет вместо них означал бы, что коалиция — отдельная
    партия, а она не отдельная.
    """
    if not entry.parts:
        draw.rectangle([x, y, x + size, y + size], fill=entry.color)
        return

    film = entry.film or entry.color
    step = size / len(entry.parts)
    for index, (_name, color, _seats) in enumerate(entry.parts):
        left = x + step * index
        # Последняя полоска дотягивается до края: иначе на дробном шаге
        # справа остаётся щель в пиксель.
        right = x + size if index == len(entry.parts) - 1 else left + step
        draw.rectangle([left, y, right, y + size],
                       fill=_blend(color, film, FILM_ALPHA))
    draw.rectangle([x, y, x + size, y + size],
                   outline=_blend(film, film, FILM_EDGE_ALPHA), width=1)


def _blend(base: str, film: str, alpha: float) -> tuple[int, int, int]:
    """Цвет `base` под плёнкой `film` — то же, что даёт наложение с альфой.

    Легенда рисуется прямо на непрозрачной картинке, отдельный слой ради
    квадратика в двадцать пикселей заводить не из-за чего.
    """
    under = _rgba(base, 1.0)
    over = _rgba(film, 1.0)
    return tuple(round(u + (o - u) * alpha) for u, o in zip(under[:3], over[:3]))


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
