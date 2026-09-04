"""Карта архипелага: округа заливкой в цвет победившей партии.

Округа рисуются своими полигонами из `district_geometry` — карта не зависит
ни от каких внешних файлов и работает сразу после установки. Раньше здесь
требовалась картинка-подложка, и в установленной программе её было попросту
некуда положить: путь вёл в Program Files, куда без прав администратора не
пишут, а переустановка стёрла бы файл.

Подложку всё ещё можно подложить фоном (спутниковый снимок под границами),
но это украшение, а не условие работы. Ищется она в папке проекта
пользователя, а не рядом с программой.

Рисуется всё на одном холсте, а не `Stack`-ом контейнеров: Flet расставляет
их в пикселях, а округа заданы долями от размера карты и должны тянуться
вместе с ней при любом размере окна.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft
import flet.canvas as cv

from ..district_geometry import DISTRICT_SHAPES, MAP_ASPECT
from . import theme
from .mount import push

#: Куда можно положить необязательную подложку. Папка проекта пользователя —
#: туда программа и так пишет, права администратора не нужны.
MAP_FILE_NAMES = ("map.png", "map.jpg", "map.jpeg", "map.webp")


def map_image_path(project_dir: Path | None = None) -> Path | None:
    """Необязательная подложка под границами округов."""
    if project_dir is None:
        return None
    for name in MAP_FILE_NAMES:
        candidate = project_dir / name
        if candidate.exists():
            return candidate
    return None


class MapChart(ft.Container):
    """Холст с картой округов.

    :param districts: `(code, название, мест, цвет|None)` — цвет None означает
                      округ без результата, он заливается серым.
    :param on_pick: зовётся с кодом округа по клику внутри его границ.
    :param height: явная высота — для маленького предпросмотра в диалоге
                   экспорта, который сам себя в ширину не растягивает.
                   Без неё холст занимает всё место, отданное раскладкой,
                   как на самом экране карты.
    """

    def __init__(self, districts=None, on_pick=None, background: Path | None = None,
                 height: float | None = None):
        self._districts = list(districts or [])
        self._on_pick = on_pick
        self._background = background
        self._width_px = 0.0
        self._height_px = 0.0
        #: Прямоугольник карты внутри холста: по нему считаются полигоны и
        #: разбирается попадание клика.
        self._rect = (0.0, 0.0, 0.0, 0.0)

        self._canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        super().__init__(
            content=ft.GestureDetector(content=self._canvas, on_tap_down=self._on_tap),
            expand=height is None,
            height=height,
        )

    def set_districts(self, districts) -> None:
        self._districts = list(districts)
        self._redraw()

    # -- размеры ------------------------------------------------------------

    def _on_resize(self, event: cv.CanvasResizeEvent) -> None:
        self._width_px = event.width
        self._height_px = event.height
        self._redraw()

    def _fit_rect(self) -> tuple[float, float, float, float]:
        """Вписывает карту в холст целиком, сохраняя пропорции.

        Именно вписывает, а не заполняет: обрезать нельзя, иначе часть округов
        уедет за край и станет недоступной для клика.
        """
        if self._width_px <= 0 or self._height_px <= 0:
            return (0.0, 0.0, 0.0, 0.0)
        width = self._width_px
        height = width / MAP_ASPECT
        if height > self._height_px:
            height = self._height_px
            width = height * MAP_ASPECT
        return ((self._width_px - width) / 2, (self._height_px - height) / 2,
                width, height)

    def _points(self, poly, left, top, width, height):
        return [(left + x * width, top + y * height) for x, y in poly]

    # -- отрисовка ----------------------------------------------------------

    def _redraw(self) -> None:
        if self._width_px <= 0 or self._height_px <= 0:
            return
        self._rect = self._fit_rect()
        left, top, width, height = self._rect
        if width <= 0:
            return

        shapes: list[cv.Shape] = []
        if self._background is not None:
            shapes.append(cv.Image(src=str(self._background), x=left, y=top,
                                   width=width, height=height))

        for code, name, seats, color in self._districts:
            polys = DISTRICT_SHAPES.get(code)
            if not polys:
                continue
            fill = color or theme.EMPTY_SEAT
            for poly in polys:
                points = self._points(poly, left, top, width, height)
                shapes.append(cv.Path(
                    [cv.Path.MoveTo(*points[0])]
                    + [cv.Path.LineTo(x, y) for x, y in points[1:]]
                    + [cv.Path.Close()],
                    paint=ft.Paint(color=fill, style=ft.PaintingStyle.FILL),
                ))
                shapes.append(cv.Path(
                    [cv.Path.MoveTo(*points[0])]
                    + [cv.Path.LineTo(x, y) for x, y in points[1:]]
                    + [cv.Path.Close()],
                    paint=ft.Paint(color="#ffffff", stroke_width=1.4,
                                   style=ft.PaintingStyle.STROKE),
                ))

        self._canvas.shapes = shapes
        push(self._canvas)

    # -- клик ---------------------------------------------------------------

    def _on_tap(self, event) -> None:
        """Определяет округ по точке нажатия — попаданием внутрь полигона.

        Холст не сообщает, по какой фигуре кликнули, поэтому проверяем сами.
        Это точнее прежнего «ближайший маркер»: мелкие городские округа лежат
        вплотную, и ближайшая точка часто оказывалась не тем округом.
        """
        if not self._on_pick:
            return
        left, top, width, height = self._rect
        if width <= 0:
            return

        px, py = event.local_position.x, event.local_position.y
        x = (px - left) / width
        y = (py - top) / height
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return

        for code, _name, _seats, _color in self._districts:
            for poly in DISTRICT_SHAPES.get(code, ()):
                if _inside(x, y, poly):
                    self._on_pick(code)
                    return


def _inside(x: float, y: float, poly) -> bool:
    """Точка внутри многоугольника — трассировка луча."""
    inside = False
    count = len(poly)
    j = count - 1
    for i in range(count):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside
