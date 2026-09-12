"""Карта: как она вписывается в кадр и что попадает в PNG."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from parlament.district_geometry import DISTRICT_SHAPES, MAP_ASPECT  # noqa: E402
from parlament.district_seed import SEED_DISTRICTS  # noqa: E402
from parlament.ui import theme  # noqa: E402
import flet.canvas as cv  # noqa: E402

from parlament.ui.map_chart import MapChart  # noqa: E402
from parlament.settlement_points import SETTLEMENT_POINTS  # noqa: E402
from parlament.ui.map_export import render_map_png  # noqa: E402
from parlament.ui.support_export import (  # noqa: E402
    BUNDLED_MAP,
    is_image,
    map_image,
    render_support_png,
)
from parlament.ui.map_frame import CONTENT_ASPECT, CONTENT_BOX, place, unplace  # noqa: E402

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


class TestNoLabelsOnTheMap(unittest.TestCase):
    """Карта — только заливка и границы, без единой буквы или цифры поверх.

    Названия округов, номера, число мандатов — всё это карту засоряло, а
    читать по надписям было особо нечего: округ и так виден по клику, а
    список партий — в легенде. Осталась чистая заливка.
    """

    def test_the_window_chart_draws_no_text(self):
        chart = MapChart(districts=sample_districts())
        chart._width_px, chart._height_px = 1400.0, 620.0
        chart._redraw()
        self.assertFalse(any(isinstance(s, cv.Text) for s in chart._canvas.shapes))


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

    def test_no_dark_text_appears_over_a_light_fill(self):
        # Обратная сторона предыдущей правки: на светлой заливке не должно
        # остаться ни одной тёмной буквы или цифры. Ни море, ни заливка, ни
        # белые границы тёмных пикселей не дают — если они появились, значит
        # что-то подписалось поверх.
        light = [(code, name, seats, "#f0e6c8")
                 for code, name, seats, _region, _places in SEED_DISTRICTS]
        image = Image.open(io.BytesIO(render_map_png(light, width=900)))
        dark = [p for p in _pixels(image.convert("RGB")) if max(p) < 90]
        self.assertFalse(dark, "на карте нашёлся текст поверх заливки")

    def test_title_and_legend_add_their_bands(self):
        bare = self.png().height
        with_extras = render_map_png(
            sample_districts(), width=900, title="Третий состав",
            legend=[("Народный союз", "#0088b0", 6, 34, 27.4)])
        taller = Image.open(io.BytesIO(with_extras)).height
        self.assertGreater(taller, bare)


class TestSupportPng(unittest.TestCase):
    """Выгрузка расстановки поддержки одной партии."""

    def png(self, marks, **kwargs) -> Image.Image:
        data = render_support_png(marks, "Партия труда", "#c41e5d",
                                  width=900, **kwargs)
        self.assertTrue(data.startswith(b"\x89PNG"))
        return Image.open(io.BytesIO(data)).convert("RGB")

    def test_the_picture_keeps_the_whole_canvas(self):
        # Кадр здесь не обрезается по архипелагу, как у выборов: подписи
        # пунктов нарисованы на самой подложке и обрезку не переживут.
        image = self.png([("Трэлавик", 6, 6)])
        self.assertEqual(image.width, 900)
        self.assertAlmostEqual(image.height, round(900 / MAP_ASPECT), delta=1)

    def test_the_party_colour_appears_on_the_map(self):
        colors = _pixels(self.png([("Трэлавик", 6, 6)]))
        self.assertIn(_rgb("#c41e5d"), colors)

    def test_without_any_marks_the_party_colour_is_only_in_the_corner(self):
        # Квадратик партии в углу остаётся, но над картой не должно быть ни
        # одного числа её цветом.
        bare = self.png([])
        top = bare.crop((0, 0, bare.width, round(bare.height * 0.8)))
        self.assertNotIn(_rgb("#c41e5d"), _pixels(top))

    def test_a_place_missing_from_the_map_is_skipped(self):
        # Пункт переименовали руками — рисовать его негде, но выгрузка всё
        # равно обязана собраться.
        self.png([("Такого пункта нет", 3, 6)])

    def test_every_known_place_has_a_point(self):
        # Разметка карты и справочник пунктов не должны расходиться: иначе
        # часть очков молча не попадала бы на картинку.
        known = set(SETTLEMENT_POINTS)
        seeded = set()
        for _code, _name, _seats, region, places in SEED_DISTRICTS:
            seeded |= set(places or ())
            if places is None:
                seeded.add(region)
        self.assertEqual(seeded - known, set())

    def test_the_bundled_map_is_used_when_the_project_has_none(self):
        # Ведущий не обязан ничего подкладывать: карта едет с приложением.
        self.assertEqual(map_image(None), BUNDLED_MAP if BUNDLED_MAP.exists() else None)

    def test_a_project_map_beats_the_bundled_one(self):
        with tempfile.TemporaryDirectory() as folder:
            own = Path(folder) / "map.png"
            Image.new("RGB", (16, 9), "#123456").save(own)
            self.assertEqual(map_image(Path(folder)), own)

    def test_a_readable_picture_passes_the_check(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 4), "#ffffff").save(buffer, "PNG")
        self.assertTrue(is_image(buffer.getvalue()))

    def test_something_that_is_not_a_picture_fails_it(self):
        self.assertFalse(is_image(b"cthulhu fhtagn"))


if __name__ == "__main__":
    unittest.main()
