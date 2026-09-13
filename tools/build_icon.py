"""Собирает assets/icon.ico из assets/icon.png.

Запускается вручную, когда меняется сама иконка:

    python tools/build_icon.py

Зачем отдельный .ico рядом с .png. Иконку приложения используют двое, и
им нужно разное:

* `flet build` берёт `assets/icon.png` и сам раскладывает её по форматам
  всех платформ — ему хватает PNG;
* окно программы на Windows берёт иконку из `page.window.icon`, и там
  нужен именно `.ico` (см. `flet.Window.icon`). PNG туда подставить
  нельзя, поэтому файл готовится заранее.

В .ico кладутся все обычные размеры сразу: Windows берёт из него тот,
который ей нужен под конкретное место — заголовок окна, панель задач,
Alt+Tab, — и если нужного размера нет, масштабирует крупный сам, заметно
хуже, чем это делает Pillow.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "icon.png"
OUT = ROOT / "assets" / "icon.ico"

#: Размеры внутри .ico — обычный набор Windows.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"нет исходной иконки: {SRC.relative_to(ROOT)}")

    with Image.open(SRC) as image:
        icon = image.convert("RGBA")
        if icon.width != icon.height:
            raise SystemExit(f"иконка должна быть квадратной, а она {icon.width}×{icon.height}")
        icon.save(OUT, format="ICO", sizes=SIZES)

    print(f"{OUT.relative_to(ROOT)}: размеров {len(SIZES)}, "
          f"{OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
