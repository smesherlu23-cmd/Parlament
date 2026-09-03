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


def centroid(polys) -> tuple[float, float]:
    """Центр тяжести фигуры — туда ставится подпись округа."""
    total = sx = sy = 0.0
    for poly in polys:
        area = cx = cy = 0.0
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            cross = x0 * y1 - x1 * y0
            area += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        area *= 0.5
        if abs(area) < 1e-9:
            continue
        total += area
        sx += cx / (6 * area) * area
        sy += cy / (6 * area) * area
    return (sx / total, sy / total) if abs(total) > 1e-9 else polys[0][0]


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
        f"#: Пропорции карты (ширина / высота) — по ним она вписывается в экран.",
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
    lines.append("#: Центр каждого округа — куда ставить подпись.")
    lines.append("DISTRICT_CENTRES: dict[int, tuple[float, float]] = {")
    for code in sorted(shapes):
        cx, cy = centroid(shapes[code])
        lines.append(f"    {code}: ({round(cx, 5)}, {round(cy, 5)}),")
    lines.append("}")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: округов {len(shapes)}, "
          f"контуров {sum(len(v) for v in shapes.values())}, "
          f"{OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
