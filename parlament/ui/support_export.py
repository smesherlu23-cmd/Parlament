"""Выгрузка поддержки одной партии на карту — PNG тем же Pillow.

Отличается от выгрузки итогов выборов (`map_export`) тем, что показывает не
результат, а расстановку сил до него: сколько очков неформальной
популярности партия набрала в каждом населённом пункте. Картинка делается
на партию: у каждой она своя, и сравнивать их между собой — дело ведущего,
а не одной перегруженной карты.

Кадр здесь — весь исходный холст 16:9, а не плотная рамка вокруг
архипелага, как у выборов. Причина простая: подписи пунктов уже нарисованы
на самой подложке, и обрезать кадр значило бы разъехаться с ними. Точки
пунктов (`settlement_points`) заданы долями того же холста, поэтому никакого
пересчёта между ними и подложкой не нужно — координата ложится один в один.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from ..district_geometry import DISTRICT_SHAPES, MAP_ASPECT
from ..settlement_points import SETTLEMENT_POINTS
from . import format as fmt
from . import theme
from .export import _font  # общий подбор шрифта: тот же Source Serif, что в окне
from .map_chart import map_image_path

_REGULAR = "SourceSerif4-Regular.ttf"
_SEMIBOLD = "SourceSerif4-SemiBold.ttf"

#: Во сколько раз запасная карта рисуется крупнее нужного (см. `map_export`).
_SUPERSAMPLE = 3

#: Насколько выше точки пункта ставится число, в долях высоты кадра. На самой
#: подложке под этой же точкой уже подписано название, и число встаёт над ним,
#: а не поверх.
_LIFT = 0.031

#: Тёмная обводка вокруг числа, в долях его кегля. Цвет партии может оказаться
#: светлым, а под ним — то песок, то лес: без обводки часть чисел терялась бы.
#: Тонкая: обводка должна отделять цифру от фона, а не превращаться в кляксу
#: вокруг неё — на карте таких чисел два-три десятка.
_HALO = 0.09


#: Карта, которая едет вместе с приложением. Без неё выгрузка поддержки
#: теряет смысл: подписи пунктов нарисованы на самой карте, и по одним
#: полигонам округов человек не поймёт, где какое село.
BUNDLED_MAP = theme.ASSETS_DIR / "map.png"


def map_image(project_dir: Path | None = None) -> Path | None:
    """Подложка для выгрузки: своя из папки проекта, иначе встроенная.

    Своя перебивает встроенную по тому же правилу, что и у карты округов
    (см. `map_chart.map_image_path`): ведущий, подложивший рядом с проектом
    свою версию карты, ожидает увидеть именно её.
    """
    own = map_image_path(project_dir)
    if own is not None:
        return own
    return BUNDLED_MAP if BUNDLED_MAP.exists() else None


def render_support_png(marks, party_name: str, party_color: str,
                       width: int = 1920, background: Path | None = None,
                       emblem: Path | None = None) -> bytes:
    """Собирает картинку поддержки одной партии.

    :param marks: `(название пункта, очки партии, запас пункта)` — только те
                  пункты, где у партии есть хотя бы одно очко.
    :param party_name: подпись в углу — чья это расстановка.
    :param party_color: цвет партии, им же пишутся числа.
    :param background: карта-подложка; без неё рисуются полигоны округов.
    :param emblem: необязательная эмблема партии, ставится в угол.
    """
    height = round(width / MAP_ASPECT)
    canvas = Image.new("RGB", (width, height), theme.MAP_SEA)

    under = _load_image(background)
    if under is not None:
        canvas.paste(under.convert("RGB").resize((width, height), Image.LANCZOS), (0, 0))
    else:
        # Подложки нет — рисуем хотя бы очертания округов, чтобы числа не
        # висели в пустоте.
        islands = _render_islands(width, height)
        canvas.paste(islands, (0, 0), islands)

    draw = ImageDraw.Draw(canvas)
    _draw_marks(draw, width, height, marks, party_color)
    _draw_corner(canvas, draw, width, height, party_name, party_color, marks, emblem)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_marks(draw: ImageDraw.ImageDraw, width: int, height: int,
                marks, color: str) -> None:
    """Числа «очки/запас» над точками пунктов."""
    size = max(10, round(width * 0.0125))
    font = _font(_SEMIBOLD, size)
    halo = max(1, round(size * _HALO))

    for name, got, capacity in marks:
        spot = SETTLEMENT_POINTS.get(name)
        if spot is None:
            # Пункт, которого нет в разметке карты: переименовали руками или
            # завели в проекте, начатом до неё. Молча пропускаем — рисовать
            # его всё равно негде.
            continue
        x, y = spot
        draw.text(
            (x * width, y * height - height * _LIFT),
            f"{got}/{capacity}", font=font, fill=color, anchor="ms",
            stroke_width=halo, stroke_fill="#000000bb",
        )


def _draw_corner(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                 width: int, height: int, party_name: str, color: str,
                 marks, emblem: Path | None) -> None:
    """Угловая плашка: чья карта и сколько всего набрано."""
    pad = round(width * 0.022)
    badge = round(width * 0.072)
    bottom = height - pad

    mark = _load_image(emblem)
    left = pad
    if mark is not None:
        mark = _fit(mark, badge)
        canvas.paste(mark, (left, bottom - mark.height),
                     mark if mark.mode == "RGBA" else None)
        left += mark.width + round(width * 0.012)
        baseline = bottom - mark.height // 2
    else:
        # Эмблемы нет — на её месте цветной квадратик партии, как в легендах
        # остальных выгрузок.
        box = round(width * 0.018)
        draw.rectangle([left, bottom - box, left + box, bottom],
                       fill=color, outline="#ffffffcc")
        left += box + round(width * 0.010)
        baseline = bottom - box // 2

    got = sum(n for _name, n, _cap in marks)
    total = sum(cap for _name, _n, cap in marks)
    name_size = max(12, round(width * 0.019))
    note_size = max(10, round(width * 0.013))

    draw.text((left, baseline - name_size * 0.15), party_name,
              font=_font(_SEMIBOLD, name_size), fill="#ffffff", anchor="ls",
              stroke_width=max(1, round(name_size * 0.055)), stroke_fill="#000000aa")
    draw.text((left, baseline + note_size * 1.25),
              f"{got} из {total} очков в {fmt.pluralize(len(marks), fmt.PLACES_IN)}",
              font=_font(_REGULAR, note_size), fill="#ffffffcc", anchor="ls",
              stroke_width=max(1, round(note_size * 0.07)), stroke_fill="#000000aa")


def _fit(image: Image.Image, box: int) -> Image.Image:
    """Вписывает картинку в квадрат со стороной `box`, не искажая пропорций."""
    image = image.convert("RGBA")
    scale = box / max(image.width, image.height)
    return image.resize((max(1, round(image.width * scale)),
                         max(1, round(image.height * scale))), Image.LANCZOS)


def is_image(data: bytes) -> bool:
    """Читается ли это как картинка.

    Проверяется до того, как файл ляжет в папку проекта: сервис про Pillow
    не знает и по одному расширению отличить картинку от чего угодно с тем
    же именем не может.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def _load_image(path: Path | None) -> Image.Image | None:
    if path is None:
        return None
    try:
        with Image.open(path) as image:
            return image.copy()
    except Exception:
        # Картинку не прочитали — выгрузка всё равно должна собраться.
        return None


def _render_islands(width: int, height: int) -> Image.Image:
    """Запасная карта из полигонов округов — когда подложки нет."""
    big = (width * _SUPERSAMPLE, height * _SUPERSAMPLE)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    for polys in DISTRICT_SHAPES.values():
        for poly in polys:
            pen.polygon([(x * big[0], y * big[1]) for x, y in poly],
                        fill=theme.EMPTY_SEAT, outline="#ffffff",
                        width=max(1, round(1.6 * width / 1600 * _SUPERSAMPLE)))
    return layer.resize((width, height), Image.LANCZOS)
