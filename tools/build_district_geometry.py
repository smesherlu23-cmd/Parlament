"""Переводит okruga.svg в модуль с полигонами округов.

Запускается вручную, когда заказчик присылает новую карту:

    python tools/build_district_geometry.py

Почему сгенерированный модуль, а не чтение SVG на лету: файл должен попасть
в собранное приложение. Питоновский модуль туда попадает всегда, а вот
судьба произвольного файла в assets зависит от упаковщика — и один раз мы
на этом уже обожглись (карта не доехала до установленной программы).

Координаты пишутся долями от размера холста (0..1), а не пикселями: карта
на экране тянется, и полигоны должны тянуться вместе с ней.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.w3.org/2000/svg}"
CMD = re.compile(r"([MLZmlz])|(-?\d+(?:\.\d+)?)")

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "okruga.svg"
OUT = ROOT / "parlament" / "district_geometry.py"

#: Номер полигона в SVG -> номер округа на картах заказчика. Внутренняя
#: нумерация файла разошлась с присланными картинками; сверено по трём
#: крупным планам (Гаффинсвик, Нивенсхолл, Саттмалвик).
SVG_TO_MAP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
              9: 10, 10: 11, 11: 15, 12: 9, 13: 13, 14: 12, 15: 14, 16: 16,
              17: 17, 18: 18, 19: 24, 20: 20, 21: 21, 22: 19, 23: 23,
              24: 26, 25: 22, 26: 25, 27: 27}


def subpaths(d: str) -> list[list[tuple[float, float]]]:
    """Контуры пути. В файле только абсолютные M/L/Z, кривых нет."""
    tokens = [(m.group(1), m.group(2)) for m in CMD.finditer(d)]
    polys, current, nums = [], [], []
    for cmd, num in tokens:
        if num is not None:
            nums.append(float(num))
            if len(nums) == 2:
                current.append((nums[0], nums[1]))
                nums = []
        elif cmd in "MmZz":
            if current:
                polys.append(current)
                current = []
    if current:
        polys.append(current)
    return [p for p in polys if len(p) >= 3]


def label_point(polys, aspect: float) -> tuple[float, float, float]:
    """Куда ставить подпись округа — точку берём внутри фигуры.

    Центр тяжести для этого не годится: у вогнутых округов он оказывается за
    их пределами. На нашей карте так выходило у шести округов из двадцати
    семи, и пять подписей ложились поверх соседа — «4» читалась на третьем
    округе.

    Берём полюс недоступности: точку внутри, максимально удалённую от
    границы. Ищем перебором по сетке с несколькими уточнениями — полигонов
    три десятка, и генерация всё равно разовая.

    Расстояния считаем в долях ширины карты: по вертикали доля «короче» во
    столько раз, каково соотношение сторон, и без поправки узкий и высокий
    округ казался бы просторнее, чем он есть.

    Возвращает точку и запас вокруг неё — по нему подпись подбирает себе
    размер, чтобы не вылезти за мелкий городской округ.
    """
    poly = max(polys, key=_area)
    xs = [x for x, _y in poly]
    ys = [y for _x, y in poly]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)

    best = ((left + right) / 2, (top + bottom) / 2)
    best_distance = -1.0
    step = max(right - left, bottom - top) / 24

    for _ in range(6):
        y = top
        while y <= bottom:
            x = left
            while x <= right:
                if _inside(x, y, poly):
                    distance = _edge_distance(x, y, poly, aspect)
                    if distance > best_distance:
                        best_distance, best = distance, (x, y)
                x += step
            y += step
        # Сужаем область вокруг найденной точки и мельчим шаг.
        left, right = best[0] - step, best[0] + step
        top, bottom = best[1] - step, best[1] + step
        step /= 4

    return best[0], best[1], best_distance


def _area(poly) -> float:
    total = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2


def _inside(x: float, y: float, poly) -> bool:
    """Точка внутри многоугольника — трассировка луча."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _edge_distance(x: float, y: float, poly, aspect: float) -> float:
    """Расстояние до ближайшей стороны в долях ширины карты.

    Чем оно больше, тем «глубже» точка внутри фигуры.
    """
    best = float("inf")
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        dx, dy = x1 - x0, (y1 - y0) / aspect
        length = dx * dx + dy * dy
        px_dx, px_dy = x - x0, (y - y0) / aspect
        t = 0.0 if length == 0 else max(0.0, min(1.0, (px_dx * dx + px_dy * dy) / length))
        best = min(best, ((px_dx - t * dx) ** 2 + (px_dy - t * dy) ** 2) ** 0.5)
    return best


def main() -> None:
    root = ET.parse(SVG).getroot()
    view = (root.get("viewBox") or "0 0 2560 1440").split()
    width, height = float(view[2]), float(view[3])

    shapes = {}
    for path in root.iter(f"{NS}path"):
        if not (path.get("id") or "").endswith("-fill"):
            continue
        code = SVG_TO_MAP[int(path.get("data-okrug"))]
        polys = [[(round(x / width, 5), round(y / height, 5)) for x, y in poly]
                 for poly in subpaths(path.get("d"))]
        shapes[code] = polys

    lines = [
        '"""Полигоны округов — сгенерировано tools/build_district_geometry.py.',
        "",
        "Руками не правится: любые изменения затрутся при следующей генерации.",
        "Координаты — доли от размера карты (0..1), начало в левом верхнем углу.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "#: Пропорции карты (ширина / высота) — по ним она вписывается в экран.",
        f"MAP_ASPECT = {width / height!r}",
        "",
        "#: Номер округа -> список контуров, контур -> список точек.",
        "DISTRICT_SHAPES: dict[int, list[list[tuple[float, float]]]] = {",
    ]
    for code in sorted(shapes):
        lines.append(f"    {code}: [")
        for poly in shapes[code]:
            pts = ", ".join(f"({x}, {y})" for x, y in poly)
            lines.append(f"        [{pts}],")
        lines.append("    ],")
    lines.append("}")
    lines.append("")
    points = {code: label_point(shapes[code], width / height) for code in sorted(shapes)}

    lines.append("#: Точка подписи округа — внутри фигуры, подальше от границ.")
    lines.append("DISTRICT_CENTRES: dict[int, tuple[float, float]] = {")
    for code, (cx, cy, _room) in points.items():
        lines.append(f"    {code}: ({round(cx, 5)}, {round(cy, 5)}),")
    lines.append("}")
    lines.append("")
    lines.append("#: Сколько места вокруг этой точки — в долях ширины карты. По нему")
    lines.append("#: подпись выбирает размер: на городском округе в несколько пикселей")
    lines.append("#: цифра обычного размера накрыла бы соседей.")
    lines.append("DISTRICT_LABEL_ROOM: dict[int, float] = {")
    for code, (_cx, _cy, room) in points.items():
        lines.append(f"    {code}: {round(room, 5)},")
    lines.append("}")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: округов {len(shapes)}, "
          f"контуров {sum(len(v) for v in shapes.values())}, "
          f"{OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
