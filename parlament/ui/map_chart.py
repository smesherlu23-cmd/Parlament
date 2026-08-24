"""Карта архипелага с маркерами округов.

Рисуется на одном холсте — как и схема зала: подложка-картинка, поверх неё
кружки округов, покрашенные в цвет победившей партии. Холст, а не `Stack` с
позиционированными контейнерами, потому что Flet расставляет их в пикселях,
а маркеры заданы долями от размера карты и должны ехать вместе с ней при
любом размере окна.

Подложка лежит в `assets/map/base.png` и в репозиторий не входит: это
игровая карта заказчика. Если файла нет, рисуется заглушка с объяснением —
приложение из-за отсутствия картинки падать не должно.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft
import flet.canvas as cv

from . import theme
from .mount import push

#: Где приложение ищет подложку. Первый существующий файл и берётся.
MAP_DIR = theme.ASSETS_DIR / "map"
MAP_CANDIDATES = ("base.png", "base.jpg", "base.jpeg", "base.webp")

#: Пропорции по умолчанию, если картинки нет и спросить не у кого.
FALLBACK_ASPECT = 16 / 9

_MARKER_RADIUS = 13          # радиус маркера при ширине карты 1000 px
_MARKER_MIN_RADIUS = 7


def map_image_path() -> Path | None:
    """Путь к подложке или None, если её не положили."""
    for name in MAP_CANDIDATES:
        candidate = MAP_DIR / name
        if candidate.exists():
            return candidate
    return None


def map_aspect_ratio() -> float:
    """Пропорции подложки (ширина / высота).

    Читаются из самого файла, а не задаются числом: карту заказчик может
    заменить на другую, и подгонять под неё код не должно требоваться.
    """
    path = map_image_path()
    if path is None:
        return FALLBACK_ASPECT
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        if width > 0 and height > 0:
            return width / height
    except Exception:
        # Битый или неизвестный формат — не повод падать при открытии экрана.
        pass
    return FALLBACK_ASPECT


class MapChart(ft.Container):
    """Холст с картой: подложка и маркеры округов.

    :param markers: `(district_id, название, мест, x, y, цвет|None)` —
                    цвет None означает округ без результата, он серый.
    :param on_pick: зовётся с id округа по клику на маркер.
    """

    def __init__(self, markers=None, on_pick=None, show_counts: bool = True):
        self._markers = list(markers or [])
        self._on_pick = on_pick
        self._show_counts = show_counts
        self._width_px = 0.0
        self._height_px = 0.0
        #: Прямоугольник картинки внутри холста — по нему считаются маркеры
        #: и разбирается попадание клика.
        self._rect = (0.0, 0.0, 0.0, 0.0)

        self._canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        super().__init__(
            content=ft.GestureDetector(content=self._canvas, on_tap_down=self._on_tap),
            expand=True,
        )

    def set_markers(self, markers) -> None:
        self._markers = list(markers)
        self._redraw()

    # -- размеры ------------------------------------------------------------

    def _on_resize(self, event: cv.CanvasResizeEvent) -> None:
        self._width_px = event.width
        self._height_px = event.height
        self._redraw()

    def _fit_rect(self) -> tuple[float, float, float, float]:
        """Вписывает картинку в холст целиком, сохраняя пропорции, и центрирует.

        Именно вписывает, а не заполняет: обрезать карту нельзя, иначе часть
        округов уедет за край и станет недоступной для клика.
        """
        if self._width_px <= 0 or self._height_px <= 0:
            return (0.0, 0.0, 0.0, 0.0)
        aspect = map_aspect_ratio()
        width = self._width_px
        height = width / aspect
        if height > self._height_px:
            height = self._height_px
            width = height * aspect
        return ((self._width_px - width) / 2, (self._height_px - height) / 2,
                width, height)

    def _marker_radius(self, width: float) -> float:
        return max(_MARKER_MIN_RADIUS, _MARKER_RADIUS * width / 1000)

    # -- отрисовка ----------------------------------------------------------

    def _redraw(self) -> None:
        if self._width_px <= 0 or self._height_px <= 0:
            return
        self._rect = self._fit_rect()
        left, top, width, height = self._rect
        if width <= 0:
            return

        shapes: list[cv.Shape] = []
        path = map_image_path()
        if path is None:
            shapes.extend(self._placeholder_shapes(left, top, width, height))
        else:
            shapes.append(cv.Image(src=str(path), x=left, y=top,
                                   width=width, height=height))

        radius = self._marker_radius(width)
        for _id, _name, seats, x, y, color in self._markers:
            cx, cy = left + x * width, top + y * height
            fill = color or theme.EMPTY_SEAT
            # Белая подложка под маркером: на пёстрой карте цветной кружок
            # без неё сливается с рельефом.
            shapes.append(cv.Circle(cx, cy, radius + 2,
                                    ft.Paint(color="#e6ffffff", style=ft.PaintingStyle.FILL)))
            shapes.append(cv.Circle(cx, cy, radius,
                                    ft.Paint(color=fill, style=ft.PaintingStyle.FILL)))
            shapes.append(cv.Circle(cx, cy, radius,
                                    ft.Paint(color="#55000000", stroke_width=1,
                                             style=ft.PaintingStyle.STROKE)))
            if self._show_counts and radius >= 9:
                shapes.append(cv.Text(
                    cx, cy, str(seats),
                    alignment=ft.Alignment.CENTER,
                    style=ft.TextStyle(
                        size=radius * 0.95,
                        font_family=theme.FONT_SEMIBOLD,
                        color=_readable_on(fill),
                    ),
                ))

        self._canvas.shapes = shapes
        push(self._canvas)

    def _placeholder_shapes(self, left, top, width, height) -> list[cv.Shape]:
        """Что показать, если подложку ещё не положили."""
        return [
            cv.Rect(left, top, width, height, 2,
                    ft.Paint(color=theme.NEUTRAL_200, style=ft.PaintingStyle.FILL)),
            cv.Rect(left, top, width, height, 2,
                    ft.Paint(color=theme.DIVIDER, stroke_width=1,
                             style=ft.PaintingStyle.STROKE)),
            cv.Text(
                left + width / 2, top + height / 2,
                f"Положите картинку карты в {MAP_DIR / 'base.png'}",
                alignment=ft.Alignment.CENTER,
                style=ft.TextStyle(size=theme.fs(13), color=theme.NEUTRAL_600),
            ),
        ]

    # -- клик ---------------------------------------------------------------

    def _on_tap(self, event) -> None:
        """Ищет ближайший маркер к точке нажатия.

        Холст не умеет сообщать, по какой фигуре кликнули, поэтому попадание
        разбираем сами — по расстоянию до центра маркера.
        """
        if not self._on_pick:
            return
        left, top, width, height = self._rect
        if width <= 0:
            return

        radius = self._marker_radius(width)
        # Палец толще маркера: небольшой запас заметно облегчает попадание.
        reach = max(radius * 1.6, 14)
        px, py = event.local_position.x, event.local_position.y

        best_id, best_distance = None, reach
        for district_id, _name, _seats, x, y, _color in self._markers:
            cx, cy = left + x * width, top + y * height
            distance = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if distance <= best_distance:
                best_id, best_distance = district_id, distance

        if best_id is not None:
            self._on_pick(best_id)


def _readable_on(background: str) -> str:
    """Чёрная или белая подпись — та, что читается на этом фоне."""
    try:
        r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return theme.TEXT
    # Формула яркости из sRGB: зелёный глаз воспринимает сильнее прочих.
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return theme.TEXT if brightness > 150 else "#ffffff"
