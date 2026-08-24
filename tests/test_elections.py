"""Тесты выборов по округам: деление мест, победители, импорт таблицы."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_seed import SEED_DISTRICTS, SEED_TOTAL_SEATS  # noqa: E402
from parlament.elections import (  # noqa: E402
    allocate_seats,
    district_winner,
    totals_by_party,
)
from parlament.model import Project  # noqa: E402
from parlament.votes_import import parse_votes_csv  # noqa: E402


class TestAllocation(unittest.TestCase):
    """Деление мест округа между партиями — метод наибольших остатков."""

    def test_splits_proportionally(self):
        # 50 / 33,3 / 16,7 % от десяти мест.
        self.assertEqual(allocate_seats({"a": 1200, "b": 800, "c": 400}, 10),
                         {"a": 5, "b": 3, "c": 2})

    def test_leader_takes_the_largest_share(self):
        allocation = allocate_seats({"a": 5000, "b": 3000, "c": 2000}, 10)
        self.assertEqual(max(allocation, key=allocation.get), "a")

    def test_no_contest_means_winner_takes_all(self):
        # Голоса только у одной партии — весь округ уходит ей.
        self.assertEqual(allocate_seats({"a": 1000}, 9), {"a": 9})

    def test_every_seat_is_handed_out(self):
        # Остаток от округления не должен пропадать: сумма всегда равна округу.
        for seats in range(1, 13):
            allocation = allocate_seats({"a": 7, "b": 5, "c": 3}, seats)
            self.assertEqual(sum(allocation.values()), seats, f"мест в округе: {seats}")

    def test_ties_go_by_register_order(self):
        # При равных голосах лишнее место достаётся той партии, что раньше
        # в справочнике, а не «какой повезёт» по случайному id.
        self.assertEqual(allocate_seats({"a": 100, "b": 100, "c": 100}, 10)["a"], 4)
        self.assertEqual(allocate_seats({"c": 100, "b": 100, "a": 100}, 10)["c"], 4)

    def test_no_votes_leaves_seats_unassigned(self):
        self.assertEqual(allocate_seats({}, 5), {})
        self.assertEqual(allocate_seats({"a": 0, "b": 0}, 5), {})

    def test_zero_seat_district_hands_out_nothing(self):
        self.assertEqual(allocate_seats({"a": 100}, 0), {})

    def test_winner_is_the_party_with_most_seats(self):
        self.assertEqual(district_winner({"a": 1200, "b": 800}, 6), "a")
        self.assertIsNone(district_winner({}, 6))

    def test_totals_add_up_across_districts(self):
        self.assertEqual(totals_by_party({"d1": {"a": 3, "b": 2}, "d2": {"a": 1}}),
                         {"a": 4, "b": 2})


class ElectionTestCase(unittest.TestCase):
    """Сервис на временном файле с партиями и округами с карты."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "выборы.parlament.json"
        self.service = ParlamentService(self.path)
        self.service.bootstrap()

        self.a = self.service.create_party(name="Народный союз", color="#0088b0")
        self.b = self.service.create_party(name="Партия труда", color="#d6006c")
        self.c = self.service.create_party(name="Аграрный блок", color="#4c7a34")
        self.by_name = {d.name: d for d in self.service.project.districts}

    @property
    def conv(self):
        return self.service.project.active_convocation


class TestDistrictsFromMap(ElectionTestCase):
    def test_seeded_from_the_game_map(self):
        self.assertEqual(len(self.service.project.districts), len(SEED_DISTRICTS))
        self.assertEqual(self.service.project.total_seats, SEED_TOTAL_SEATS)

    def test_parliament_size_equals_sum_of_districts(self):
        # Карта — источник истины: разъехаться эти числа не должны.
        self.assertEqual(sum(d.seats for d in self.service.project.districts),
                         self.service.project.total_seats)

    def test_markers_sit_inside_the_map_image(self):
        for district in self.service.project.districts:
            self.assertTrue(0.0 <= district.x <= 1.0, district.name)
            self.assertTrue(0.0 <= district.y <= 1.0, district.name)

    def test_old_projects_keep_their_own_size(self):
        # Проект, созданный до карты, не должен внезапно получить округа и
        # чужой размер парламента — там места набраны руками.
        old = Project.from_dict({"schemaVersion": 1, "totalSeats": 120,
                                 "parties": [], "convocations": []})
        self.assertEqual(old.total_seats, 120)
        self.assertEqual(old.districts, [])


class TestRunElection(ElectionTestCase):
    def test_results_fill_the_parliament(self):
        self.service.run_election(self.conv.id, {
            self.by_name["Гаффинсвик центр"].id: {self.a.id: 5000, self.b.id: 3000,
                                                  self.c.id: 2000},
            self.by_name["Саттмалвик центр"].id: {self.b.id: 9000},
        })
        # 10 мест: 5/3/2, плюс 9 мест целиком «Партии труда».
        self.assertEqual(self.conv.seats, {self.a.id: 5, self.b.id: 12, self.c.id: 2})
        self.assertTrue(self.conv.has_election)

    def test_winners_drive_the_map(self):
        self.service.run_election(self.conv.id, {
            self.by_name["Гаффинсвик центр"].id: {self.a.id: 5000, self.b.id: 3000},
            self.by_name["Судбригг"].id: {self.c.id: 600, self.a.id: 400},
        })
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(winners[self.by_name["Гаффинсвик центр"].id], self.a.id)
        self.assertEqual(winners[self.by_name["Судбригг"].id], self.c.id)

    def test_districts_without_results_stay_uncoloured(self):
        self.service.run_election(self.conv.id, {
            self.by_name["Судбригг"].id: {self.a.id: 100},
        })
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(len(winners), 1)
        self.assertNotIn(self.by_name["Гаффинсвик центр"].id, winners)

    def test_single_district_can_be_edited_without_touching_the_rest(self):
        first = self.by_name["Судбригг"].id
        second = self.by_name["Гаффинсвик центр"].id
        self.service.set_district_votes(self.conv.id, first, {self.a.id: 100})
        self.service.set_district_votes(self.conv.id, second, {self.b.id: 100})
        self.assertEqual(self.conv.seats, {self.a.id: 2, self.b.id: 10})

    def test_emptying_a_district_takes_its_seats_back(self):
        district = self.by_name["Гаффинсвик центр"].id
        self.service.set_district_votes(self.conv.id, district, {self.a.id: 100})
        self.assertEqual(self.conv.seats, {self.a.id: 10})
        self.service.set_district_votes(self.conv.id, district, {})
        self.assertEqual(self.conv.seats, {})

    def test_rerunning_replaces_previous_results(self):
        # Выборы — это результат целиком, а не точечная правка: округа, о
        # которых новые данные молчат, обнуляются.
        self.service.run_election(self.conv.id, {
            self.by_name["Судбригг"].id: {self.a.id: 100},
            self.by_name["Гаффинсвик центр"].id: {self.a.id: 100},
        })
        self.service.run_election(self.conv.id, {
            self.by_name["Судбригг"].id: {self.b.id: 100},
        })
        self.assertEqual(self.conv.seats, {self.b.id: 2})

    def test_clearing_returns_to_manual_mode(self):
        self.service.run_election(self.conv.id, {
            self.by_name["Судбригг"].id: {self.a.id: 100}})
        self.service.clear_election(self.conv.id)
        self.assertEqual(self.conv.seats, {})
        self.assertFalse(self.conv.has_election)

    def test_never_exceeds_the_parliament(self):
        # Даже если заполнить все округа, сумма ровно равна размеру палаты.
        self.service.run_election(self.conv.id, {
            d.id: {self.a.id: 600, self.b.id: 400}
            for d in self.service.project.districts
        })
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_results_survive_restart(self):
        self.service.run_election(self.conv.id, {
            self.by_name["Гаффинсвик центр"].id: {self.a.id: 5000, self.b.id: 3000}})
        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(again.project.active_convocation.seats, self.conv.seats)
        self.assertTrue(again.project.active_convocation.has_election)

    def test_unknown_district_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.set_district_votes(self.conv.id, "нет-такого", {self.a.id: 1})

    def test_negative_votes_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.set_district_votes(
                self.conv.id, self.by_name["Судбригг"].id, {self.a.id: -5})

    def test_fractional_votes_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.set_district_votes(
                self.conv.id, self.by_name["Судбригг"].id, {self.a.id: 1.5})


class TestVotesImport(ElectionTestCase):
    def table(self, body: str) -> str:
        return "Округ,Народный союз,Партия труда,Аграрный блок\n" + body

    def parse(self, text: str):
        return parse_votes_csv(
            text,
            {d.name: d.id for d in self.service.project.districts},
            {p.name: p.id for p in self.service.project.parties},
        )

    def test_reads_a_plain_table(self):
        result = self.parse(self.table(
            "Гаффинсвик центр,5000,3000,2000\nСаттмалвик центр,,9000,\n"))
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.districts_filled, 2)
        self.assertEqual(result.votes[self.by_name["Саттмалвик центр"].id],
                         {self.b.id: 9000})

    def test_imported_table_can_be_applied(self):
        result = self.parse(self.table("Гаффинсвик центр,5000,3000,2000\n"))
        self.service.run_election(self.conv.id, result.votes)
        self.assertEqual(self.conv.seats, {self.a.id: 5, self.b.id: 3, self.c.id: 2})

    def test_semicolons_and_spaced_numbers(self):
        # Русский Excel сохраняет через «;» и разбивает тысячи пробелами.
        result = self.parse("Округ;Народный союз;Партия труда\n"
                            "Гаффинсвик центр;12 500;3 000\n")
        self.assertEqual(result.votes[self.by_name["Гаффинсвик центр"].id],
                         {self.a.id: 12500, self.b.id: 3000})

    def test_names_match_loosely(self):
        result = self.parse("Округ,  народный СОЮЗ \n  гаффинсвик  центр ,100\n")
        self.assertEqual(result.votes[self.by_name["Гаффинсвик центр"].id],
                         {self.a.id: 100})

    def test_unknown_names_are_reported_not_swallowed(self):
        result = self.parse(self.table("Атлантида,1,2,3\n"))
        self.assertEqual(result.votes, {})
        self.assertTrue(any("Атлантида" in w for w in result.warnings))

    def test_unknown_party_column_is_reported(self):
        result = self.parse("Округ,Партия чужая\nСудбригг,100\n")
        self.assertTrue(any("Партия чужая" in w for w in result.warnings))

    def test_garbage_cell_is_reported(self):
        result = self.parse(self.table("Судбригг,абв,10,\n"))
        self.assertTrue(any("не число" in w for w in result.warnings))
        # Остальные клетки строки при этом разобрались.
        self.assertEqual(result.votes[self.by_name["Судбригг"].id], {self.b.id: 10})

    def test_empty_file_explains_the_format(self):
        self.assertTrue(self.parse("").warnings)


if __name__ == "__main__":
    unittest.main()
