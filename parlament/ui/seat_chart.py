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
    #: Угол места на дуге, в радианах: π — левый край полукруга, 0 — правый.
    #: Нужен, чтобы найти сектор коалиции, не пересчитывая геометрию заново.
    angle: float = 0.0
    #: Радиус ряда, в котором стоит место. Нужен плёнке коалиции: границу
    #: блока она ведёт по каждому ряду отдельно.
    ring: float = 0.0
    #: Цвет плёнки коалиции над этим местом; `None` — место вне блока.
    film: str | None = None


def compute_seats(
    total: int = DEFAULT_TOTAL_SEATS,
    rows: int = 5,
    dist: list[tuple[str, int]] | None = None,
    empty_color: str = theme.EMPTY_SEAT,
) -> list[Seat]:
    """Считает координаты всех мест.

    :param dist: в порядке отрисовки (слева направо) — либо пары «цвет партии,
                 число мест», либо тройки, где третьим идёт цвет плёнки
                 коалиции над этими местами (`None` — партия сама по себе).
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
    # Рядом — плёнка коалиции для каждого места, чтобы отрисовка знала, какие
    # места накрыты одним блоком, не пересчитывая ничего заново.
    colors: list[str] = []
    films: list[str | None] = []
    for entry in dist:
        color, seats = entry[0], entry[1]
        film = entry[2] if len(entry) > 2 else None
        count = max(0, int(seats))
        colors.extend([color] * count)
        films.extend([film] * count)
    empty = seat_count - len(colors)
    colors.extend([empty_color] * empty)
    films.extend([None] * empty)

    placed: list[tuple[float, float]] = []
    for row_index, count_in_row in enumerate(counts):
        for j in range(count_in_row):
            angle = math.pi * (1 - (j + 0.5) / count_in_row)
            placed.append((angle, radii[row_index]))

    # Слева направо: угол π — левый край полукруга, 0 — правый.
    placed.sort(key=lambda item: item[0], reverse=True)

    # Размер места ограничен с двух сторон: расстоянием между рядами и шагом
    # соседей по дуге. Раньше учитывалось только первое, а размер 12,5 был
    # подобран под палату на 120 мест — на 124 соседи по дуге стоят ближе,
    # и кружки наезжали друг на друга.
    gap = (_OUTER_RADIUS - _INNER_RADIUS) / (row_count - 1)
    along_arc = min((radii[i] * math.pi / counts[i]
                     for i in range(row_count) if counts[i]), default=math.inf)
    seat_radius = min(gap * 0.30, along_arc * 0.45, 12.5)

    return [
        Seat(
            x=_CENTER_X + radius * math.cos(angle),
            y=_CENTER_Y - radius * math.sin(angle),
            radius=seat_radius,
            color=colors[index],
            angle=angle,
            ring=radius,
            film=films[index],
        )
        for index, (angle, radius) in enumerate(placed)
    ]


#: Плотность плёнки коалиции. Достаточно, чтобы блок читался одним пятном,
#: и достаточно мало, чтобы под ней различались цвета самих партий: коалиция
#: — не новая партия, а то, как договорились нынешние.
FILM_ALPHA = 0.26
#: Плотнее той же краски — для квадратика коалиции в легенде, где плёнку
#: нужно обозначить рамкой на два десятка пикселей.
FILM_EDGE_ALPHA = 0.55


@dataclass(frozen=True)
class FilmSector:
    """Сектор, накрытый плёнкой одной коалиции.

    Места красятся подряд слева направо, поэтому блок — это всегда сплошной
    кусок дуги: от `start_angle` (левее) до `end_angle` (правее), через все
    ряды сразу. Радиусы у всех секторов одинаковы, но лежат здесь же, чтобы
    отрисовке не пришлось знать про устройство схемы.
    """

    color: str
    start_angle: float
    end_angle: float
    inner_radius: float
    outer_radius: float


def film_sectors(seats: list[Seat]) -> list[FilmSector]:
    """Находит куски дуги, накрытые плёнками коалиций, — по ряду за раз.

    Границу между блоками ведём посередине между соседями **внутри одного
    ряда**, а не по всей схеме сразу. Соблазнительно накрыть блок одним
    сплошным сектором от края до края, но шаг мест по дуге в рядах разный:
    прямой радиальный срез, посчитанный по общему порядку, проходит через
    кружки соседних рядов и закрашивает их наполовину. Полоса на ряд такого
    не допускает — граница всегда ровно между двумя местами этого ряда, — а
    полосы соседних рядов смыкаются вплотную и на глаз читаются одним блоком.

    Края полукруга (π и 0) отдаём крайним блокам целиком: место с краю и так
    стоит у самой кромки.
    """
    if not seats:
        return []

    rings = sorted({seat.ring for seat in seats})
    # Полуширина полосы — половина расстояния между рядами: полосы соседних
    # рядов тогда сходятся без щели, а место в кружок всегда внутри (его
    # радиус заведомо меньше, см. `seat_radius`).
    step = (rings[1] - rings[0]) if len(rings) > 1 else _OUTER_RADIUS - _INNER_RADIUS
    half = step / 2

    sectors: list[FilmSector] = []
    for ring in rings:
        row = [seat for seat in seats if seat.ring == ring]
        row.sort(key=lambda seat: seat.angle, reverse=True)

        start = 0
        for index in range(len(row) + 1):
            if index < len(row) and row[index].film == row[start].film:
                continue
            film = row[start].film
            if film is not None:
                left = (math.pi if start == 0
                        else (row[start - 1].angle + row[start].angle) / 2)
                right = (0.0 if index == len(row)
                         else (row[index - 1].angle + row[index].angle) / 2)
                sectors.append(FilmSector(film, left, right,
                                          ring - half, ring + half))
            start = index
    return sectors


def with_alpha(color: str, alpha: float) -> str:
    """«#rrggbb» + прозрачность → «#aarrggbb», как принято во Flet и Flutter."""
    channel = max(0, min(255, round(alpha * 255)))
    return f"#{channel:02x}{color.lstrip('#')}"


def sector_polygon(sector: FilmSector, steps: int = 48, pad: float = 0.0
                   ) -> list[tuple[float, float]]:
    """Сектор точками в координатах макета — для отрисовки многоугольником.

    Pillow не умеет заливать кольцевой сектор напрямую, а толщина дуги у него
    считается внутрь от габаритов, и подогнать её точно под кольцо непросто.
    Ломаная из полусотни точек на глаз неотличима от дуги и ведёт себя
    предсказуемо на любом разрешении.
    """
    span = sector.start_angle - sector.end_angle
    count = max(2, int(steps * max(span, 0.0) / math.pi) + 2)
    # `pad` раздвигает полосу по радиусу: полосы соседних рядов тогда заходят
    # друг на друга, и между ними не остаётся щели в пиксель от округления.
    outer = sector.outer_radius + pad
    inner = max(0.0, sector.inner_radius - pad)
    outer_arc = [
        (_CENTER_X + outer * math.cos(sector.start_angle - span * i / (count - 1)),
         _CENTER_Y - outer * math.sin(sector.start_angle - span * i / (count - 1)))
        for i in range(count)
    ]
    inner_arc = [
        (_CENTER_X + inner * math.cos(sector.end_angle + span * i / (count - 1)),
         _CENTER_Y - inner * math.sin(sector.end_angle + span * i / (count - 1)))
        for i in range(count)
    ]
    return outer_arc + inner_arc


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
    # Плёнки — последними, поверх мест: сквозь них должны просвечивать цвета
    # партий, а не наоборот.
    shapes.extend(_film_shapes(seats, scale, offset_x))
    return shapes


def _film_shapes(seats: list[Seat], scale: float,
                 offset_x: float) -> list[cv.Shape]:
    """Кольцевые сектора коалиций — дугой с толстой обводкой.

    Толщина обводки равна толщине кольца, поэтому дуга радиусом посередине
    закрашивает его целиком: отдельного примитива под кольцевой сектор в
    Canvas нет, а собирать его из Path ради того же результата незачем.
    """
    shapes: list[cv.Shape] = []
    for sector in film_sectors(seats):
        thickness = sector.outer_radius - sector.inner_radius
        middle = (sector.outer_radius + sector.inner_radius) / 2
        radius = middle * scale
        box_x = offset_x + _CENTER_X * scale - radius
        box_y = _CENTER_Y * scale - radius
        # Угол на схеме растёт против часовой стрелки, у Canvas — по ней:
        # отсюда минус, а размах остаётся положительным.
        start = -sector.start_angle
        sweep = sector.start_angle - sector.end_angle
        shapes.append(cv.Arc(
            box_x, box_y, radius * 2, radius * 2, start, sweep,
            paint=ft.Paint(color=with_alpha(sector.color, FILM_ALPHA),
                           stroke_width=thickness * scale,
                           style=ft.PaintingStyle.STROKE),
        ))
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
