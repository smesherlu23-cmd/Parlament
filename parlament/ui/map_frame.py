"""Как карта вписывается в кадр: плотная рамка, подписи, цвет текста.

Полигоны в `district_geometry` заданы долями от исходного холста 16:9, но сам
архипелаг занимает в нём не всё: сверху и снизу оставалась четверть пустоты,
и карта выходила мелкой, а пустые поля — большими. Здесь считается плотная
рамка вокруг всех округов, и обе отрисовки — окно и PNG — кладут карту по
ней. Считается на лету, потому что `district_geometry` генерируется
инструментом и руками не правится.

Общий модуль на оба рисовальщика ещё и держит их в согласии: рамка, подписи и
выбор цвета текста должны совпадать, иначе выгруженная картинка отличалась бы
от того, что человек видел на экране.
"""

from __future__ import annotations

from ..district_geometry import DISTRICT_CENTRES, DISTRICT_SHAPES, MAP_ASPECT

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


def label_point(code: int, left: float, top: float,
                width: float, height: float) -> tuple[float, float] | None:
    """Куда ставить подпись округа — точка из геометрии, уже в пикселях."""
    centre = DISTRICT_CENTRES.get(code)
    if centre is None:
        return None
    return place(centre[0], centre[1], left, top, width, height)


def _spans(code: int, y: float, left: float, top: float,
           width: float, height: float) -> list[tuple[float, float]]:
    """Куски округа на горизонтали `y` — трассировка луча по его контурам."""
    crossings: list[float] = []
    for poly in DISTRICT_SHAPES.get(code, ()):
        pixels = [place(px, py, left, top, width, height) for px, py in poly]
        count = len(pixels)
        for i in range(count):
            x1, y1 = pixels[i]
            x2, y2 = pixels[(i + 1) % count]
            if (y1 > y) != (y2 > y):
                crossings.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    crossings.sort()
    return list(zip(crossings[::2], crossings[1::2]))


#: Сколько горизонталей просмотреть вокруг точки подписи и как далеко от неё
#: отходить (в долях высоты кадра). Точка из геометрии гарантированно внутри
#: округа, но не обязательно в самом широком его месте — а подписи нужно
#: именно широкое.
_PROBE_STEPS = 9
_PROBE_REACH = 0.02


def room_at(code: int, x: float, y: float, left: float, top: float,
            width: float, height: float) -> float:
    """Сколько места вширь у точки `(x, y)` внутри округа, в пикселях.

    Нужна отдельно от `label_spot`, потому что строки подписи стоят выше и
    ниже найденной точки, а округ там уже другой ширины: меряем ровно ту
    строку, куда ляжет текст.
    """
    for begin, finish in _spans(code, y, left, top, width, height):
        if begin <= x <= finish:
            return 2 * min(x - begin, finish - x)
    return 0.0


def label_spot(code: int, left: float, top: float, width: float,
               height: float) -> tuple[float, float, float] | None:
    """Где подписать округ и сколько там места: `(x, y, ширина)` в пикселях.

    Точка подписи из геометрии лежит внутри округа, но нередко в узком его
    месте, и название вылезало бы в море. Поэтому смотрим несколько
    горизонталей вокруг неё и берём ту, где кусок округа под точкой шире
    всего; подпись съезжает на пару пикселей, зато остаётся на суше.

    Место меряем честной трассировкой, а не габаритами: у длинного изогнутого
    острова габаритный прямоугольник широкий, а под подписью может быть узко.
    """
    point = label_point(code, left, top, width, height)
    if point is None:
        return None
    x, y = point

    best: tuple[float, float, float] | None = None
    for step in range(_PROBE_STEPS):
        offset = (step / (_PROBE_STEPS - 1) - 0.5) * 2 * _PROBE_REACH * height
        probe_y = y + offset
        room = room_at(code, x, probe_y, left, top, width, height)
        if best is None or room > best[2]:
            best = (x, probe_y, room)
    return best


#: Запас между подписью и берегом, в долях доступной ширины: впритык
#: название читается плохо, да и соседний округ начинается сразу за линией.
NAME_MARGIN = 0.86


def text_color(fill: str, dark: str, light: str) -> str:
    """Цвет подписи под заливкой округа: тёмный на светлой, светлый на тёмной.

    Обводку рисовать не приходится, а подпись остаётся читаемой на любой
    партийной краске — в том числе на не выбранных ещё пользователем.
    """
    value = fill.lstrip("#")
    if len(value) != 6:
        return dark
    red, green, blue = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    # Относительная яркость по восприятию: зелёный весит больше синего.
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return dark if luminance > 0.55 else light
