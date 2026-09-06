"""Как карта вписывается в кадр: плотная рамка вокруг архипелага.

Полигоны в `district_geometry` заданы долями от исходного холста 16:9, но сам
архипелаг занимает в нём не всё: сверху и снизу оставалась четверть пустоты,
и карта выходила мелкой, а пустые поля — большими. Здесь считается плотная
рамка вокруг всех округов, и обе отрисовки — окно и PNG — кладут карту по
ней. Считается на лету, потому что `district_geometry` генерируется
инструментом и руками не правится.

Общий модуль на оба рисовальщика ещё и держит их в согласии: рамка должна
совпадать, иначе выгруженная картинка отличалась бы от того, что человек
видел на экране.
"""

from __future__ import annotations

from ..district_geometry import DISTRICT_SHAPES, MAP_ASPECT

#: Поле вокруг архипелага, в долях его размера. Немного воздуха нужно: без
#: него береговая линия упирается в самый край кадра.
_PADDING = 0.04


def _content_box() -> tuple[float, float, float, float]:
    xs = [x for polys in DISTRICT_SHAPES.values() for poly in polys for x, _ in poly]
    ys = [y for polys in DISTRICT_SHAPES.values() for poly in polys for _, y in poly]
    if not xs or not ys:
        return (0.0, 0.0, 1.0, 1.0)
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    pad_x = (right - left) * _PADDING
    pad_y = (bottom - top) * _PADDING
    return (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)


#: Плотная рамка вокруг архипелага: `(left, top, right, bottom)` в долях
#: исходного холста.
CONTENT_BOX = _content_box()

#: Пропорции этой рамки (ширина / высота) — по ним карта вписывается в кадр.
#: Доли по X и Y считаны от разных сторон исходного холста, поэтому одного
#: их отношения мало: его надо домножить на пропорции самого холста.
#: Выходит заметно шире исходных 16:9 — архипелаг вытянут с запада на восток.
CONTENT_ASPECT = ((CONTENT_BOX[2] - CONTENT_BOX[0])
                  / max(1e-9, CONTENT_BOX[3] - CONTENT_BOX[1])) * MAP_ASPECT


def place(x: float, y: float, left: float, top: float,
          width: float, height: float) -> tuple[float, float]:
    """Точку геометрии — в пиксели прямоугольника, отданного под карту."""
    box_left, box_top, box_right, box_bottom = CONTENT_BOX
    span_x = max(1e-9, box_right - box_left)
    span_y = max(1e-9, box_bottom - box_top)
    return (left + (x - box_left) / span_x * width,
            top + (y - box_top) / span_y * height)


def unplace(px: float, py: float, left: float, top: float,
            width: float, height: float) -> tuple[float, float]:
    """Обратное преобразование — для разбора клика по карте."""
    box_left, box_top, box_right, box_bottom = CONTENT_BOX
    return (box_left + (px - left) / max(1e-9, width) * (box_right - box_left),
            box_top + (py - top) / max(1e-9, height) * (box_bottom - box_top))
