"""Тесты статистики выборов: острова, где бороться, что решило результат."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_seed import island_of, islands  # noqa: E402
from parlament.elections import PartyResult, THRESHOLD_PERCENT  # noqa: E402
from parlament.statistics import (  # noqa: E402
    Battleground,
    attribution,
    battlegrounds,
    island_rows,
)


class TestIslandRows(unittest.TestCase):
    """Сводка по островам из готового разбора."""

    def rows(self, **kwargs):
        districts = [("d1", "Запад", 5), ("d2", "Запад", 3), ("d3", "Восток", 4)]
        results = {
            "d1": {"a": PartyResult(share=60.0), "b": PartyResult(share=40.0)},
            "d2": {"a": PartyResult(share=20.0), "b": PartyResult(share=80.0)},
            "d3": {"a": PartyResult(share=50.0), "b": PartyResult(share=50.0)},
        }
        allocation = {"d1": {"a": 3, "b": 2}, "d2": {"b": 3}, "d3": {"a": 2, "b": 2}}
        populations = {"d1": 30000.0, "d2": 10000.0, "d3": 40000.0}
        data = dict(districts=districts, results=results, allocation=allocation,
                    populations=populations)
        data.update(kwargs)
        return island_rows(**data)

    def test_islands_keep_the_order_they_appear_in(self):
        self.assertEqual([r.name for r in self.rows()], ["Запад", "Восток"])

    def test_mandates_and_people_are_summed_over_the_island(self):
        west = self.rows()[0]
        self.assertEqual(west.seats, 8)
        self.assertEqual(west.population, 40000.0)

    def test_mandates_of_a_party_are_summed_over_the_island(self):
        west = self.rows()[0]
        self.assertEqual(west.by_party, {"a": 3, "b": 5})

    def test_shares_are_weighted_by_people_not_by_districts(self):
        # В большом округе у «a» 60 %, в маленьком 20 %. Простое среднее дало
        # бы 40 %, а по людям — (60·30 + 20·10) / 40 = 50 %.
        west = self.rows()[0]
        self.assertAlmostEqual(west.shares["a"], 50.0)
        self.assertAlmostEqual(west.shares["b"], 50.0)

    def test_people_per_seat_shows_how_uneven_the_map_is(self):
        west, east = self.rows()
        self.assertAlmostEqual(west.people_per_seat, 5000.0)
        self.assertAlmostEqual(east.people_per_seat, 10000.0)

    def test_an_island_without_people_does_not_divide_by_zero(self):
        rows = self.rows(populations={})
        self.assertEqual(rows[0].shares, {})
        self.assertEqual(rows[0].people_per_seat, 0.0)


class TestBattlegrounds(unittest.TestCase):
    """Округа глазами одной партии."""

    def ground(self, party="a") -> list[Battleground]:
        districts = [("d1", "Первый", "Запад", 5), ("d2", "Второй", "Запад", 4)]
        results = {
            "d1": {"a": PartyResult(share=30.0), "b": PartyResult(share=70.0)},
            "d2": {"a": PartyResult(share=3.0), "b": PartyResult(share=97.0)},
        }
        allocation = {"d1": {"a": 2, "b": 3}, "d2": {"b": 4}}
        points = {"d1": {"a": 2, "b": 4}, "d2": {"b": 1}}
        capacities = {"d1": 6, "d2": 12}
        return battlegrounds(party, districts, results, allocation, points, capacities)

    def test_one_row_per_district(self):
        self.assertEqual([b.name for b in self.ground()], ["Первый", "Второй"])

    def test_the_gap_is_the_distance_to_the_winner(self):
        first = self.ground()[0]
        self.assertEqual(first.leader, "b")
        self.assertAlmostEqual(first.gap, 40.0)

    def test_the_winner_has_no_gap_to_itself(self):
        first = self.ground(party="b")[0]
        self.assertEqual(first.leader, "b")
        self.assertAlmostEqual(first.gap, 0.0)

    def test_a_share_under_the_threshold_is_flagged(self):
        first, second = self.ground()
        self.assertFalse(first.missed_threshold)
        self.assertTrue(second.missed_threshold)
        self.assertLess(second.share, THRESHOLD_PERCENT)

    def test_free_points_are_what_nobody_took(self):
        first, second = self.ground()
        self.assertEqual((first.points, first.free, first.capacity), (2, 0, 6))
        # Во втором округе роздано одно очко из двенадцати.
        self.assertEqual((second.points, second.free, second.capacity), (0, 11, 12))

    def test_an_empty_district_has_no_leader(self):
        rows = battlegrounds("a", [("d1", "Пустой", "Запад", 3)], {}, {}, {}, {"d1": 6})
        self.assertIsNone(rows[0].leader)
        self.assertEqual(rows[0].gap, 0.0)


class TestAttribution(unittest.TestCase):
    """Сколько мандатов дало или отняло каждое слагаемое."""

    def test_a_factor_that_changed_nothing_shows_zero(self):
        results = {"d1": {"a": PartyResult(base=60.0, share=60.0),
                          "b": PartyResult(base=40.0, share=40.0)}}
        table = attribution(results, {"d1": 10})
        self.assertEqual(table["a"], {"national": 0, "island": 0, "wobble": 0})

    def test_a_mood_that_won_mandates_is_counted(self):
        # Без «настроения» у «a» было бы 50/50 и по пять мандатов; с ним она
        # берёт большинство округа.
        results = {"d1": {"a": PartyResult(base=50.0, national=30.0, share=80.0),
                          "b": PartyResult(base=50.0, share=20.0)}}
        table = attribution(results, {"d1": 10})
        self.assertGreater(table["a"]["national"], 0)
        self.assertEqual(table["a"]["national"], -table["b"]["national"])

    def test_the_local_modifier_is_not_among_the_factors(self):
        # Местная поправка в очках и уже сидит внутри базы — вынуть её из
        # одного разбора нельзя, и придумывать для неё число мы не станем.
        results = {"d1": {"a": PartyResult(base=60.0, modifier=3.0, share=60.0)}}
        self.assertNotIn("modifier", attribution(results, {"d1": 5})["a"])

    def test_an_empty_roll_gives_an_empty_table(self):
        self.assertEqual(attribution({}, {}), {})


class StatsServiceTestCase(unittest.TestCase):
    """Сервис на временном проекте с разыгранными выборами."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "игра.parlament.json"
        self.service = ParlamentService(self.path)
        self.service.bootstrap()
        self.a = self.service.create_party("Аграрный блок", "#4c7a34", "АБ")
        self.b = self.service.create_party("Партия труда", "#c41e5d", "ПТ")
        self.conv = self.service.project.active_convocation

    def give(self, district_name: str, party, points: int) -> None:
        district = next(d for d in self.service.project.districts
                        if d.name == district_name)
        self.service.set_support(district.id, district.settlements[0].id,
                                 party.id, points)

    def roll(self, **kwargs):
        return self.service.roll_election(self.conv.id, rng=random.Random(3), **kwargs)


class TestStatsThroughTheService(StatsServiceTestCase):
    def test_without_an_election_everything_is_empty(self):
        # Считать нечего: у состава, набранного руками, нет ни долей, ни разбора.
        self.assertEqual(self.service.island_stats(self.conv.id), [])
        self.assertEqual(self.service.battlegrounds(self.conv.id, self.a.id), [])
        self.assertEqual(self.service.attribution(self.conv.id), {})

    def test_islands_cover_the_whole_map(self):
        self.give("Судбригг", self.a, 4)
        self.roll()
        rows = self.service.island_stats(self.conv.id)
        self.assertEqual([r.name for r in rows], islands())
        self.assertEqual(sum(r.seats for r in rows), self.service.project.total_seats)

    def test_island_people_add_up_to_the_whole_country(self):
        self.give("Судбригг", self.a, 4)
        self.roll()
        total = sum(r.population for r in self.service.island_stats(self.conv.id))
        self.assertAlmostEqual(total, 1_000_000.0, places=0)

    def test_battlegrounds_cover_every_district(self):
        self.give("Судбригг", self.a, 4)
        self.roll()
        ground = self.service.battlegrounds(self.conv.id, self.a.id)
        self.assertEqual(len(ground), len(self.service.project.districts))

    def test_a_district_the_party_worked_shows_its_points(self):
        self.give("Судбригг", self.a, 4)
        self.roll()
        ground = self.service.battlegrounds(self.conv.id, self.a.id)
        worked = next(b for b in ground if b.name == "Судбригг")
        self.assertEqual(worked.points, 4)
        district = next(d for d in self.service.project.districts
                        if d.name == "Судбригг")
        self.assertEqual(worked.island, island_of(district.region))

    def test_an_unknown_party_is_refused(self):
        self.give("Судбригг", self.a, 4)
        self.roll()
        with self.assertRaises(ValidationError):
            self.service.battlegrounds(self.conv.id, "нет такой")

    def test_a_mood_across_the_country_shows_up_in_the_attribution(self):
        self.give("Судбригг", self.a, 4)
        self.roll(national={self.a.id: 40.0})
        table = self.service.attribution(self.conv.id)
        self.assertGreater(table[self.a.id]["national"], 0)

    def test_the_same_numbers_come_back_for_an_archived_convocation(self):
        # Статистика считается из разбора, а он лежит в созыве — значит и у
        # архивного созыва она та же, что была в день выборов.
        self.give("Судбригг", self.a, 4)
        self.roll()
        before = [r.by_party for r in self.service.island_stats(self.conv.id)]
        self.service.fix_convocation("Второй состав")
        after = [r.by_party for r in self.service.island_stats(self.conv.id)]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
