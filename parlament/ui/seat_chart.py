"""Полукруглая схема парламента.

Геометрия повторяет компонент SeatChart из макета, чтобы приложение выглядело
один в один с дизайном:

  - места лежат на `rows` концентрических дугах между внутренним и внешним
    радиусом, радиусы распределены линейно;
  - мест в ряду — пропорционально его радиусу (то есть длине дуги), остаток от
    округления достаётся рядам с наибольшей дробной частью;
  - внутри ряда места стоят через равный угол; затем все места сортируются по
    углу (слева направо) и последовательно красятся цветами партий.

Расчёт (`compute_seats`) отделён от отрисовки: его же использует экспорт в PNG,
который рисует не на Canvas, а средствами Pillow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import flet as ft
import flet.canvas as cv

from ..model import DEFAULT_TOTAL_SEATS
from . import theme
from .mount import push

#: Система координат макета; всё остальное — масштабирование этой сетки.
VIEWBOX_WIDTH = 630.0
VIEWBOX_HEIGHT = 330.0

_CENTER_X = 315.0
_CENTER_Y = 315.0
_INNER_RADIUS = 128.0
_OUTER_RADIUS = 300.0

_SEAT_STROKE = theme.BG
_SEAT_STROKE_WIDTH = 1.2


@dataclass(frozen=True)
class Seat:
    """Одно место в координатах макета (630×330)."""

    x: float
    y: float
    radius: float
    color: str


def compute_seats(
    total: int = DEFAULT_TOTAL_SEATS,
    rows: int = 5,
    dist: list[tuple[str, int]] | None = None,
    empty_color: str = theme.EMPTY_SEAT,
) -> list[Seat]:
    """Считает координаты всех мест.

    :param dist: пары «цвет — число мест» в порядке отрисовки (слева направо)
    """
    seat_count = max(1, int(total))
    row_count = max(2, int(rows))
    dist = dist or []

    # Радиус каждого ряда — линейно от внутреннего к внешнему.
    radii = [
        _INNER_RADIUS + (_OUTER_RADIUS - _INNER_RADIUS) * i / (row_count - 1)
        for i in range(row_count)
    ]

    # Мест в ряду — пропорционально радиусу; остаток раздаём по наибольшей
    # дробной части, чтобы в сумме получилось ровно `total`.
    radii_sum = sum(radii)
    exact = [seat_count * r / radii_sum for r in radii]
    counts = [math.floor(v) for v in exact]
    shortfall = seat_count - sum(counts)
    by_remainder = sorted(
        range(row_count), key=lambda i: exact[i] - math.floor(exact[i]), reverse=True
    )
    for k in range(shortfall):
        counts[by_remainder[k % row_count]] += 1

    # Плоский список цветов: сначала места партий по порядку, затем пустые.
    colors: list[str] = []
    for color, seats in dist:
        colors.extend([color] * max(0, int(seats)))
    colors.extend([empty_color] * (seat_count - len(colors)))

    placed: list[tuple[float, float]] = []
    for row_index, count_in_row in enumerate(counts):
        for j in range(count_in_row):
            angle = math.pi * (1 - (j + 0.5) / count_in_row)
            placed.append((angle, radii[row_index]))

    # Слева направо: угол π — левый край полукруга, 0 — правый.
    placed.sort(key=lambda item: item[0], reverse=True)

    gap = (_OUTER_RADIUS - _INNER_RADIUS) / (row_count - 1)
    seat_radius = min(gap * 0.30, 12.5)

    return [
        Seat(
            x=_CENTER_X + radius * math.cos(angle),
            y=_CENTER_Y - radius * math.sin(angle),
            radius=seat_radius,
            color=colors[index],
        )
        for index, (angle, radius) in enumerate(placed)
    ]


def chart_height_for_width(width: float) -> float:
    """Высота схемы при заданной ширине — пропорции макета сохраняются."""
    return width * VIEWBOX_HEIGHT / VIEWBOX_WIDTH


def _shapes(seats: list[Seat], scale: float, offset_x: float = 0.0) -> list[cv.Shape]:
    shapes: list[cv.Shape] = []
    for seat in seats:
        x = offset_x + seat.x * scale
        y = seat.y * scale
        r = seat.radius * scale
        shapes.append(cv.Circle(x, y, r, ft.Paint(color=seat.color)))
        # Тонкая обводка цветом фона — соседние места разных партий не сливаются.
        shapes.append(cv.Circle(x, y, r, ft.Paint(
            color=_SEAT_STROKE,
            stroke_width=_SEAT_STROKE_WIDTH * scale,
            style=ft.PaintingStyle.STROKE,
        )))
    return shapes


class SeatChart(ft.Container):
    """Схема, которая сама подстраивается под ширину, отданную ей раскладкой."""

    def __init__(self, total: int = DEFAULT_TOTAL_SEATS, rows: int = 5,
                 dist: list[tuple[str, int]] | None = None,
                 opacity: float = 1.0, height: float | None = None):
        self._total = total
        self._rows = rows
        self._dist = dist or []
        self._canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        self._width_px = 0.0
        self._height_px = 0.0
        # Явная высота (предпросмотр в диалоге экспорта) отменяет подгонку.
        self._fixed_height = height is not None
        super().__init__(content=self._canvas, opacity=opacity, height=height)

    def set_data(self, total: int, rows: int, dist: list[tuple[str, int]]) -> None:
        self._total, self._rows, self._dist = total, rows, dist
        self._redraw()

    def _on_resize(self, event: cv.CanvasResizeEvent) -> None:
        self._width_px = event.width
        self._height_px = event.height
        self._fit_height()
        self._redraw()

    def _fit_height(self) -> None:
        """Держит высоту по пропорциям макета.

        Иначе схема растягивается на всю отданную ей высоту, рисунок остаётся
        наверху, а легенда уезжает вниз. Высота выставляется один раз на каждую
        ширину: повторный `on_resize` придёт с той же величиной и ничего не
        поменяет, так что цикла не возникает.
        """
        if self._fixed_height or self._width_px <= 0:
            return
        wanted = chart_height_for_width(self._width_px)
        if self.height is None or abs(self.height - wanted) > 0.5:
            self.height = wanted
            push(self)

    def _redraw(self) -> None:
        if self._width_px <= 0:
            return

        # Вписываем полукруг в отданную область целиком и центрируем по
        # горизонтали: при заданной извне высоте (предпросмотр в диалоге
        # экспорта) масштаба по одной ширине не хватает и рисунок обрезается.
        scale = self._width_px / VIEWBOX_WIDTH
        if self._height_px > 0:
            scale = min(scale, self._height_px / VIEWBOX_HEIGHT)
        offset_x = (self._width_px - VIEWBOX_WIDTH * scale) / 2

        seats = compute_seats(self._total, self._rows, self._dist)
        self._canvas.shapes = _shapes(seats, scale, offset_x)
        push(self._canvas)
