"""Карта: как она вписывается в кадр, где стоят подписи и что попадает в PNG."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from parlament.district_geometry import (  # noqa: E402
    DISTRICT_CENTRES,
    DISTRICT_SHAPES,
    MAP_ASPECT,
)
from parlament.district_seed import SEED_DISTRICTS  # noqa: E402
from parlament.ui import theme  # noqa: E402
import flet.canvas as cv  # noqa: E402

from parlament.ui.map_chart import MapChart, _inside  # noqa: E402
from parlament.ui.map_export import render_map_png  # noqa: E402
from parlament.ui.map_frame import (  # noqa: E402
    CONTENT_ASPECT,
    CONTENT_BOX,
    label_spot,
    place,
    room_at,
    text_color,
    unplace,
)

_COLORS = ["#0088b0", "#d6006c", "#4c7a34", "#c8621a", "#3b4a8c"]


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _pixels(image: Image.Image) -> set[tuple[int, int, int]]:
    """Все цвета картинки. Через `tobytes`, а не `getdata`: последний в
    свежих Pillow объявлен устаревшим и сорит предупреждениями."""
    raw = image.tobytes()
    return {tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)}


def sample_districts():
    return [(code, name, seats, _COLORS[i % len(_COLORS)])
            for i, (code, name, seats, _r, _p) in enumerate(SEED_DISTRICTS)]


class TestFrame(unittest.TestCase):
    """Кадр обрезан по самому архипелагу, а не по исходному холсту."""

    def test_the_frame_hugs_the_islands(self):
        # В исходном холсте 16:9 архипелаг занимал по высоте три четверти:
        # сверху и снизу оставались пустые поля, и карта выходила мелкой.
        left, top, right, bottom = CONTENT_BOX
        xs = [x for polys in DISTRICT_SHAPES.values() for p in polys for x, _ in p]
        ys = [y for polys in DISTRICT_SHAPES.values() for p in polys for _, y in p]
        self.assertLessEqual(left, min(xs))
        self.assertGreaterEqual(right, max(xs))
        self.assertLessEqual(top, min(ys))
        self.assertGreaterEqual(bottom, max(ys))
        # Но не сильно шире: поле вокруг берега небольшое.
        self.assertLess(min(xs) - left, 0.06)
        self.assertLess(min(ys) - top, 0.06)

    def test_the_frame_is_wider_than_the_original_canvas(self):
        # Архипелаг вытянут с запада на восток, поэтому плотная рамка заметно
        # шире исходных 16:9. Если это перестанет выполняться, значит доли по
        # X и Y снова считают в одном масштабе — а они от разных сторон.
        self.assertGreater(CONTENT_ASPECT, MAP_ASPECT)
        self.assertAlmostEqual(CONTENT_ASPECT, 2.26, places=1)

    def test_placing_a_point_and_taking_it_back_gives_the_same_point(self):
        for x, y in ((0.2, 0.3), (0.5, 0.5), (0.9, 0.8)):
            px, py = place(x, y, 40.0, 10.0, 800.0, 400.0)
            back = unplace(px, py, 40.0, 10.0, 800.0, 400.0)
            self.assertAlmostEqual(back[0], x, places=9)
            self.assertAlmostEqual(back[1], y, places=9)

    def test_the_islands_fill_the_frame_edge_to_edge(self):
        # Ради этого рамку и обрезали: пустых полос по краям остаться не
        # должно — иначе карта опять нарисуется мелкой.
        points = [place(x, y, 0.0, 0.0, 1000.0, 500.0)
                  for polys in DISTRICT_SHAPES.values() for p in polys for x, y in p]
        left = min(px for px, _ in points)
        right = max(px for px, _ in points)
        top = min(py for _, py in points)
        bottom = max(py for _, py in points)
        self.assertLess(left, 1000.0 * 0.05)
        self.assertGreater(right, 1000.0 * 0.95)
        self.assertLess(top, 500.0 * 0.05)
        self.assertGreater(bottom, 500.0 * 0.95)


class TestLabels(unittest.TestCase):
    """Подписи округов: точки для них считаны, но раньше не рисовались вовсе."""

    def test_every_district_gets_a_spot(self):
        for code, _name, _seats, _region, _places in SEED_DISTRICTS:
            self.assertIsNotNone(label_spot(code, 0.0, 0.0, 1600.0, 700.0),
                                 f"округ {code} без места под подпись")

    def test_the_spot_stays_inside_its_own_district(self):
        # Подпись съезжает с точки из геометрии в поисках широкого места —
        # но съехать за берег она не должна.
        for code, name, _seats, _region, _places in SEED_DISTRICTS:
            x, y, _room = label_spot(code, 0.0, 0.0, 1600.0, 700.0)
            geo = unplace(x, y, 0.0, 0.0, 1600.0, 700.0)
            self.assertTrue(
                any(_inside(geo[0], geo[1], poly) for poly in DISTRICT_SHAPES[code]),
                f"подпись «{name}» уехала за пределы округа")

    def test_the_spot_is_at_least_as_wide_as_the_original_point(self):
        # Смысл поиска: место под подпись не должно стать хуже того, что
        # даёт точка из геометрии.
        for code, _name, _seats, _region, _places in SEED_DISTRICTS:
            _x, _y, room = label_spot(code, 0.0, 0.0, 1600.0, 700.0)
            cx, cy = DISTRICT_CENTRES[code]
            px, py = place(cx, cy, 0.0, 0.0, 1600.0, 700.0)
            self.assertGreaterEqual(room + 1e-6,
                                    room_at(code, px, py, 0.0, 0.0, 1600.0, 700.0))

    def test_room_outside_the_district_is_nothing(self):
        code = SEED_DISTRICTS[0][0]
        self.assertEqual(room_at(code, -500.0, 100.0, 0.0, 0.0, 1600.0, 700.0), 0.0)

    def test_ink_flips_with_the_fill(self):
        # Обводку не рисуем — вместо неё цвет буквы по яркости заливки, и
        # подпись читается на любой партийной краске.
        self.assertEqual(text_color("#f2e9c8", "#201e1d", "#ffffff"), "#201e1d")
        self.assertEqual(text_color("#1f3a93", "#201e1d", "#ffffff"), "#ffffff")
        self.assertEqual(text_color("не цвет", "#201e1d", "#ffffff"), "#201e1d")


class TestNoDigitsOnTheMap(unittest.TestCase):
    """На карте только названия округов.

    Номер округа и число мандатов её засоряли: читать по ним нечего — номер
    и так подписан на игровой карте, а мандаты видны в разборе по клику.
    """

    def labels(self) -> list[str]:
        chart = MapChart(districts=sample_districts())
        chart._width_px, chart._height_px = 1400.0, 620.0
        chart._redraw()
        return [s.value for s in chart._canvas.shapes if isinstance(s, cv.Text)]

    def test_not_a_single_label_is_a_number(self):
        for value in self.labels():
            self.assertFalse(value.strip().isdigit(),
                             f"на карте осталась цифра «{value}»")

    def test_every_label_is_a_district_name(self):
        names = {name for _c, name, _s, _r, _p in SEED_DISTRICTS}
        shown = self.labels()
        self.assertTrue(shown, "карта осталась без подписей вовсе")
        for value in shown:
            self.assertIn(value, names)

    def test_a_district_gets_at_most_one_label(self):
        # Раньше под названием стояла вторая строка с мандатами.
        shown = self.labels()
        self.assertEqual(len(shown), len(set(shown)))


class TestMapPng(unittest.TestCase):
    """Выгруженная картинка карты."""

    def png(self, **kwargs) -> Image.Image:
        data = render_map_png(sample_districts(), width=900, **kwargs)
        return Image.open(io.BytesIO(data)).convert("RGB")

    def test_the_picture_keeps_the_frame_proportions(self):
        image = self.png()
        self.assertEqual(image.width, 900)
        self.assertAlmostEqual(image.height, round(900 / CONTENT_ASPECT), delta=1)

    def test_the_sea_is_painted_under_the_islands(self):
        # Раньше острова висели на том же цвете, что и поля вокруг; а когда
        # море появилось, оно чуть не уехало в чёрный — слой RGBA вставлялся
        # без маски. Проверяем сам угол картинки.
        image = self.png()
        self.assertEqual(image.getpixel((3, image.height - 3)),
                         _rgb(theme.MAP_SEA))

    def test_the_districts_are_actually_painted(self):
        colors = _pixels(self.png())
        for value in _COLORS:
            self.assertIn(_rgb(value), colors, f"цвет {value} на карте не встретился")

    def test_labels_reach_the_picture(self):
        # Точки подписей лежали в геометрии, были покрыты тестом — и не
        # рисовались нигде: карта была немой.
        #
        # Красим все округа светлым: тогда подпись выходит тёмной (см.
        # `text_color`), а тёмных пикселей на такой карте больше взяться
        # неоткуда — ни море, ни заливка, ни белые границы их не дают.
        light = [(code, name, seats, "#f0e6c8")
                 for code, name, seats, _region, _places in SEED_DISTRICTS]
        image = Image.open(io.BytesIO(render_map_png(light, width=900)))
        dark = [p for p in _pixels(image.convert("RGB")) if max(p) < 90]
        self.assertTrue(dark, "на карте нет ни одной подписи")

    def test_title_and_legend_add_their_bands(self):
        bare = self.png().height
        with_extras = render_map_png(
            sample_districts(), width=900, title="Третий состав",
            legend=[("Народный союз", "#0088b0", 6, 34)])
        taller = Image.open(io.BytesIO(with_extras)).height
        self.assertGreater(taller, bare)


if __name__ == "__main__":
    unittest.main()
