"""Переводит settlements.svg в модуль с точками населённых пунктов.

Запускается вручную, когда заказчик присылает новую разметку:

    python tools/build_settlement_points.py

Почему сгенерированный модуль, а не чтение SVG на лету — ровно по той же
причине, что и у полигонов округов (см. `build_district_geometry.py`):
питоновский модуль доезжает до собранного приложения всегда, а судьба
произвольного файла рядом с ним зависит от упаковщика.

В самом SVG точка пункта — кружок, а подпись к нему стоит чуть выше, ровно
над центром. Берём именно кружок: подпись сдвинута, чтобы не наезжать на
точку, и её координата — не то место, где пункт стоит на карте.

Координаты пишутся долями от размера холста (0..1), как и полигоны: холст
у обоих файлов один и тот же (2560×1440), поэтому точки и округа ложатся в
одну систему координат и никакого пересчёта между ними не нужно.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.w3.org/2000/svg}"

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "settlements.svg"
OUT = ROOT / "parlament" / "settlement_points.py"


def main() -> None:
    root = ET.parse(SVG).getroot()
    view = (root.get("viewBox") or "0 0 2560 1440").split()
    width, height = float(view[2]), float(view[3])

    circles = [(float(c.get("cx")), float(c.get("cy")))
               for c in root.iter(f"{NS}circle")]
    names = [(t.text or "").strip() for t in root.iter(f"{NS}text")]
    if len(circles) != len(names):
        raise SystemExit(f"кружков {len(circles)}, подписей {len(names)} — "
                         "в файле должно быть поровну")

    points = {}
    for name, (cx, cy) in zip(names, circles):
        if not name:
            raise SystemExit("пустая подпись у кружка — нечем назвать точку")
        if name in points:
            raise SystemExit(f"«{name}» встречается в файле дважды")
        points[name] = (round(cx / width, 5), round(cy / height, 5))

    lines = [
        '"""Точки населённых пунктов — сгенерировано tools/build_settlement_points.py.',
        "",
        "Руками не правится: любые изменения затрутся при следующей генерации.",
        "Координаты — доли от размера карты (0..1), начало в левом верхнем углу,",
        "холст тот же, что и у полигонов округов в `district_geometry`.",
        "",
        "Город здесь один на все свои избирательные округа — как и копилка очков",
        "(см. `Project.district_support`): точка стоит в центре метрополии, а не",
        "в каждом её округе отдельно.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "#: Название пункта -> точка на карте.",
        "SETTLEMENT_POINTS: dict[str, tuple[float, float]] = {",
    ]
    for name in sorted(points):
        x, y = points[name]
        lines.append(f'    "{name}": ({x}, {y}),')
    lines.append("}")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: пунктов {len(points)}, "
          f"{OUT.stat().st_size} байт")


if __name__ == "__main__":
    main()
