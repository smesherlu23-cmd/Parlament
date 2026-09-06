"""Коалиции: состав блоками, плёнка на схеме и операции сервиса."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.coalitions import blocs, chart_distribution  # noqa: E402
from parlament.model import Coalition, MIN_COALITION, Party, Project  # noqa: E402
from parlament.ui.seat_chart import compute_seats, film_sectors  # noqa: E402


def party(pid: str, name: str, color: str) -> Party:
    return Party(id=pid, name=name, color=color)


class TestBlocs(unittest.TestCase):
    """Как состав раскладывается на блоки."""

    def setUp(self):
        self.a = party("a", "Народный союз", "#0088b0")
        self.b = party("b", "Партия труда", "#d6006c")
        self.c = party("c", "Аграрный блок", "#4c7a34")
        self.d = party("d", "Consilium", "#c8621a")
        self.parties = [self.a, self.b, self.c, self.d]
        self.seats = {"a": 34, "b": 22, "c": 12, "d": 31}

    def left(self, *members: str) -> Coalition:
        return Coalition(id="k1", name="Левый блок", color="#7a3fb5",
                         members=list(members))

    def test_without_coalitions_every_party_is_its_own_bloc(self):
        result = blocs(self.parties, self.seats)
        self.assertEqual([b.name for b in result],
                         ["Народный союз", "Consilium", "Партия труда", "Аграрный блок"])
        self.assertTrue(all(b.film is None for b in result))
        self.assertTrue(all(not b.is_coalition for b in result))

    def test_coalition_weighs_as_much_as_its_members_together(self):
        result = blocs(self.parties, self.seats, [self.left("a", "b", "c")])
        self.assertEqual(result[0].name, "Левый блок")
        self.assertEqual(result[0].seats, 68)
        self.assertTrue(result[0].is_coalition)
        self.assertEqual(result[0].film, "#7a3fb5")

    def test_a_party_inside_a_bloc_does_not_stand_apart(self):
        # Иначе её места посчитались бы дважды: и в блоке, и отдельно.
        result = blocs(self.parties, self.seats, [self.left("a", "b")])
        self.assertEqual([b.name for b in result],
                         ["Левый блок", "Consilium", "Аграрный блок"])
        self.assertEqual(sum(b.seats for b in result), sum(self.seats.values()))

    def test_members_are_ordered_by_weight_inside_the_bloc(self):
        result = blocs(self.parties, self.seats, [self.left("c", "a", "b")])
        self.assertEqual([m.name for m in result[0].members],
                         ["Народный союз", "Партия труда", "Аграрный блок"])

    def test_parties_without_seats_are_left_out(self):
        result = blocs(self.parties, {"a": 10}, [])
        self.assertEqual([b.name for b in result], ["Народный союз"])

    def test_a_bloc_where_nobody_has_seats_is_left_out(self):
        result = blocs(self.parties, {"d": 31}, [self.left("a", "b")])
        self.assertEqual([b.name for b in result], ["Consilium"])

    def test_a_member_without_seats_does_not_take_a_slot(self):
        result = blocs(self.parties, {"a": 34, "b": 0, "c": 12},
                       [self.left("a", "b", "c")])
        self.assertEqual([m.name for m in result[0].members],
                         ["Народный союз", "Аграрный блок"])
        self.assertEqual(result[0].seats, 46)

    def test_the_same_party_cannot_be_counted_by_two_blocs(self):
        # Сервис такого не допускает, но файл правят руками: второй блок
        # получает лишь то, что не занято первым.
        second = Coalition(id="k2", name="Второй", color="#111111",
                           members=["a", "d"])
        result = blocs(self.parties, self.seats, [self.left("a", "b"), second])
        self.assertEqual(sum(b.seats for b in result), sum(self.seats.values()))
        by_name = {b.name: b for b in result}
        self.assertEqual([m.name for m in by_name["Второй"].members], ["Consilium"])

    def test_chart_puts_the_members_of_a_bloc_side_by_side(self):
        # Плёнка ложится сплошным куском дуги только если места блока идут
        # подряд — иначе она накрыла бы чужие.
        result = chart_distribution(blocs(self.parties, self.seats,
                                          [self.left("a", "c")]))
        films = [film for _color, _seats, film in result]
        self.assertEqual(films, ["#7a3fb5", "#7a3fb5", None, None])


class TestFilmGeometry(unittest.TestCase):
    """Плёнка на схеме зала: что она накрывает и чего не задевает."""

    def sectors(self, dist, total=60, rows=4):
        return film_sectors(compute_seats(total, rows, dist))

    def test_a_district_without_coalitions_has_no_film(self):
        self.assertEqual(self.sectors([("#111111", 30), ("#222222", 30)]), [])

    def test_a_bloc_gets_a_band_in_every_row_it_reaches(self):
        found = self.sectors([("#111111", 30, "#ff0000"), ("#222222", 30)])
        self.assertTrue(found)
        self.assertTrue(all(s.color == "#ff0000" for s in found))
        # Полоса на ряд, а не одна на всю схему.
        self.assertEqual(len({(s.inner_radius, s.outer_radius) for s in found}), 4)

    def test_bands_of_neighbouring_rows_touch_without_a_gap(self):
        found = sorted(self.sectors([("#111111", 30, "#ff0000"), ("#222222", 30)]),
                       key=lambda s: s.inner_radius)
        for lower, upper in zip(found, found[1:]):
            self.assertAlmostEqual(lower.outer_radius, upper.inner_radius, places=6)

    def test_the_film_never_reaches_a_seat_outside_the_bloc(self):
        # Прямой радиальный срез, посчитанный по общему порядку мест, проходил
        # через кружки соседних рядов и красил их наполовину: шаг по дуге в
        # рядах разный. Границу ведём по каждому ряду отдельно — вот проверка,
        # что чужое место под плёнку больше не попадает.
        seats = compute_seats(124, 5, [("#111111", 60, "#ff0000"),
                                       ("#222222", 40), ("#333333", 24)])
        sectors = film_sectors(seats)
        for seat in seats:
            if seat.film is not None:
                continue
            for sector in sectors:
                inside_ring = sector.inner_radius < seat.ring < sector.outer_radius
                inside_arc = sector.end_angle <= seat.angle <= sector.start_angle
                self.assertFalse(
                    inside_ring and inside_arc,
                    f"место вне блока под плёнкой: угол {seat.angle:.3f}")

    def test_every_seat_of_the_bloc_is_covered(self):
        seats = compute_seats(124, 5, [("#111111", 60, "#ff0000"), ("#222222", 64)])
        sectors = film_sectors(seats)
        for seat in seats:
            if seat.film is None:
                continue
            covered = any(
                s.inner_radius <= seat.ring <= s.outer_radius
                and s.end_angle <= seat.angle <= s.start_angle
                for s in sectors)
            self.assertTrue(covered, f"место блока без плёнки: угол {seat.angle:.3f}")

    def test_two_blocs_keep_their_own_colours(self):
        found = self.sectors([("#111111", 20, "#ff0000"), ("#222222", 20),
                              ("#333333", 20, "#00ff00")])
        self.assertEqual({s.color for s in found}, {"#ff0000", "#00ff00"})


class CoalitionServiceCase(unittest.TestCase):
    """Сервис на временном файле с четырьмя партиями и розданными местами."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "коалиции.parlament.json"
        self.service = ParlamentService(self.path)
        self.service.bootstrap()

        self.a = self.service.create_party("Народный союз", "#0088b0")
        self.b = self.service.create_party("Партия труда", "#d6006c")
        self.c = self.service.create_party("Аграрный блок", "#4c7a34")
        self.d = self.service.create_party("Consilium", "#c8621a")
        for party_, seats in ((self.a, 34), (self.b, 22), (self.c, 12), (self.d, 31)):
            self.service.set_seats(self.conv.id, party_.id, seats)

    @property
    def conv(self):
        return self.service.project.active_convocation


class TestCoalitionService(CoalitionServiceCase):
    def test_a_bloc_is_assembled_and_weighs_its_members(self):
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id])
        first = self.service.blocs(self.conv.id)[0]
        self.assertEqual((first.name, first.seats), ("Левый блок", 56))

    def test_one_party_is_not_a_bloc(self):
        with self.assertRaises(ValidationError) as ctx:
            self.service.create_coalition(self.conv.id, "Соло", "#7a3fb5", [self.a.id])
        self.assertIn(str(MIN_COALITION), str(ctx.exception))

    def test_a_party_cannot_sit_in_two_blocs(self):
        # Иначе её места вошли бы в оба, и сумма блоков перевалила бы за
        # размер палаты — «большинство» стало бы выдумкой.
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id])
        with self.assertRaises(ValidationError) as ctx:
            self.service.create_coalition(self.conv.id, "Правый блок", "#c8621a",
                                          [self.a.id, self.d.id])
        self.assertIn("Левый блок", str(ctx.exception))

    def test_an_unknown_party_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                          [self.a.id, "нет такой"])

    def test_a_repeated_party_does_not_count_twice(self):
        made = self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                             [self.a.id, self.b.id, self.a.id])
        self.assertEqual(made.members, [self.a.id, self.b.id])
        self.assertEqual(self.service.blocs(self.conv.id)[0].seats, 56)

    def test_editing_swaps_the_membership(self):
        made = self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                             [self.a.id, self.b.id])
        self.service.update_coalition(self.conv.id, made.id, "Широкий блок",
                                      "#111111", [self.a.id, self.c.id, self.d.id])
        first = self.service.blocs(self.conv.id)[0]
        self.assertEqual((first.name, first.seats, first.film),
                         ("Широкий блок", 77, "#111111"))

    def test_editing_may_keep_its_own_members(self):
        # Проверка «партия уже занята» не должна ловить сам этот блок.
        made = self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                             [self.a.id, self.b.id])
        self.service.update_coalition(self.conv.id, made.id, "Левый блок",
                                      "#7a3fb5", [self.a.id, self.b.id, self.c.id])
        self.assertEqual(self.service.blocs(self.conv.id)[0].seats, 68)

    def test_disbanding_leaves_the_seats_where_they_were(self):
        made = self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                             [self.a.id, self.b.id])
        before = dict(self.conv.seats)
        self.service.delete_coalition(self.conv.id, made.id)
        self.assertEqual(self.conv.seats, before)
        self.assertEqual(self.conv.coalitions, [])

    def test_deleting_a_party_takes_it_out_of_its_bloc(self):
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id, self.c.id])
        self.service.delete_party(self.b.id)
        self.assertEqual(self.conv.coalitions[0].members, [self.a.id, self.c.id])
        self.assertEqual(self.service.blocs(self.conv.id)[0].seats, 46)

    def test_a_bloc_left_with_one_party_is_disbanded(self):
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id])
        self.service.delete_party(self.b.id)
        self.assertEqual(self.conv.coalitions, [])
        self.assertEqual(self.conv.seats.get(self.a.id), 34)

    def test_blocs_survive_a_restart(self):
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id])
        again = ParlamentService(self.path)
        again.bootstrap()
        conv = again.project.active_convocation
        self.assertEqual(len(conv.coalitions), 1)
        self.assertEqual(conv.coalitions[0].name, "Левый блок")
        self.assertEqual(again.blocs(conv.id)[0].seats, 56)

    def test_a_bloc_belongs_to_its_own_convocation(self):
        # С кем партия дружит — свойство созыва: в следующем составе расклад
        # бывает совсем другой, а история должна помнить тогдашний.
        self.service.create_coalition(self.conv.id, "Левый блок", "#7a3fb5",
                                      [self.a.id, self.b.id])
        old = self.conv.id
        fresh = self.service.fix_convocation("Второй состав")
        self.assertEqual(fresh.coalitions, [])
        self.assertEqual(len(self.service.project.convocation(old).coalitions), 1)


class TestBrokenCoalitionsInTheFile(unittest.TestCase):
    """Файл проекта правят руками — блоки из него не должны ломать состав."""

    def project(self, coalitions_raw: list[dict]) -> Project:
        return Project.from_dict({
            "schemaVersion": 2, "totalSeats": 124, "rows": 5,
            "parties": [{"id": "a", "name": "А", "color": "#0088b0"},
                        {"id": "b", "name": "Б", "color": "#d6006c"}],
            "convocations": [{"id": "c1", "number": 1, "name": "Первый состав",
                              "seats": {"a": 34, "b": 22},
                              "coalitions": coalitions_raw}],
        })

    def test_a_member_that_no_longer_exists_is_dropped(self):
        project = self.project([{"id": "k1", "name": "Блок", "color": "#7a3fb5",
                                 "members": ["a", "b", "утерянная"]}])
        self.assertEqual(project.convocations[0].coalitions[0].members, ["a", "b"])

    def test_a_bloc_left_with_one_party_is_dropped(self):
        project = self.project([{"id": "k1", "name": "Блок", "color": "#7a3fb5",
                                 "members": ["a", "утерянная"]}])
        self.assertEqual(project.convocations[0].coalitions, [])

    def test_a_bloc_without_an_id_is_ignored(self):
        project = self.project([{"name": "Безымянный", "members": ["a", "b"]}])
        self.assertEqual(project.convocations[0].coalitions, [])


if __name__ == "__main__":
    unittest.main()
