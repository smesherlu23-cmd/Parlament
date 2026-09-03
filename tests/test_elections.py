"""Тесты выборов по округам: деление мест, победители, импорт таблицы."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_seed import SEED_DISTRICTS, SEED_TOTAL_SEATS  # noqa: E402
from parlament.elections import (  # noqa: E402
    MAX_ROLL,
    MIN_ROLL,
    PartyRoll,
    allocate_seats,
    district_winner,
    roll_dice,
    shares,
    support_modifier,
    totals_by_party,
    weights,
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


class TestRollMechanic(unittest.TestCase):
    """Розыгрыш голосов: бросок 1–10, модификаторы, перевод в проценты."""

    def test_example_from_the_brief(self):
        # Заказчик задал разбор на числах: 3, 5, 7 при сумме 15.
        rolls = {"a": PartyRoll(roll=3), "b": PartyRoll(roll=5), "c": PartyRoll(roll=7)}
        percent = {k: round(v, 1) for k, v in shares(rolls).items()}
        self.assertEqual(percent, {"a": 20.0, "b": 33.3, "c": 46.7})

    def test_dice_stay_within_one_and_ten(self):
        rng = random.Random(1)
        values = [roll_dice(rng) for _ in range(2000)]
        self.assertEqual(min(values), MIN_ROLL)
        self.assertEqual(max(values), MAX_ROLL)

    def test_support_is_points_per_settlement(self):
        self.assertEqual(support_modifier(9, 3), 3.0)
        self.assertAlmostEqual(support_modifier(7, 3), 2.3333, places=3)
        # Округ без НП не должен ронять расчёт делением на ноль.
        self.assertEqual(support_modifier(5, 0), 0.0)

    def test_support_is_added_whole_not_capped(self):
        # По уточнению заказчика: «сколько поддержки — столько и бонус».
        self.assertEqual(PartyRoll(roll=4, support=3.0).total, 7.0)

    def test_debate_bonus_can_be_any_number(self):
        self.assertEqual(PartyRoll(roll=5, debate=4).total, 9.0)
        self.assertEqual(PartyRoll(roll=5, debate=-3).total, 2.0)

    def test_agitation_adds_one(self):
        self.assertEqual(PartyRoll(roll=5, agitation=True).total, 6.0)
        self.assertEqual(PartyRoll(roll=5, agitation=False).total, 5.0)

    def test_all_modifiers_stack(self):
        self.assertAlmostEqual(
            PartyRoll(roll=4, support=7 / 3, debate=-2, agitation=True).total,
            4 + 7 / 3 - 2 + 1)

    def test_total_never_goes_below_zero(self):
        # Отрицательный вес вычитал бы голоса у соседей и ломал пропорцию.
        self.assertEqual(PartyRoll(roll=1, debate=-9).total, 0.0)
        self.assertEqual(PartyRoll(roll=2, support=-5).total, 0.0)

    def test_party_at_zero_gets_no_votes(self):
        rolls = {"a": PartyRoll(roll=6), "b": PartyRoll(roll=1, debate=-5)}
        self.assertEqual(set(weights(rolls)), {"a"})
        self.assertEqual(shares(rolls), {"a": 100.0})

    def test_shares_always_add_up_to_a_hundred(self):
        rng = random.Random(7)
        for _ in range(200):
            rolls = {f"p{i}": PartyRoll(roll=roll_dice(rng), support=rng.random() * 4,
                                        debate=rng.randint(-3, 3),
                                        agitation=bool(rng.getrandbits(1)))
                     for i in range(rng.randint(2, 6))}
            total = sum(shares(rolls).values())
            if total:
                self.assertAlmostEqual(total, 100.0, places=6)

    def test_fractional_weights_still_fill_the_district(self):
        # Средняя поддержка почти всегда дробная — места всё равно раздаются
        # все до единого.
        rolls = {"a": PartyRoll(roll=3, support=1 / 3),
                 "b": PartyRoll(roll=5, support=2 / 3),
                 "c": PartyRoll(roll=7, support=1 / 7)}
        seats = allocate_seats(weights(rolls), 9)
        self.assertEqual(sum(seats.values()), 9)

    def test_roll_survives_a_round_trip(self):
        original = PartyRoll(roll=6, support=2.5, debate=-1, agitation=True)
        self.assertEqual(PartyRoll.from_dict(original.to_dict()), original)

    def test_broken_stored_roll_does_not_crash(self):
        # Файл проекта правится руками — мусор в полях не должен ронять загрузку.
        self.assertEqual(PartyRoll.from_dict({"roll": "ой", "support": None}),
                         PartyRoll(roll=0, support=0.0))


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


class TestSettlementsAndSupport(ElectionTestCase):
    """Населённые пункты и очки неформальной популярности."""

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Гаффинсвик центр"]

    def test_districts_start_without_settlements(self):
        # В присланной карте НП нет — список наполняет пользователь.
        self.assertEqual(self.district.settlements, [])

    def test_points_add_up_across_settlements(self):
        first = self.service.add_settlement(self.district.id, "Гаффинсвик-Сити")
        second = self.service.add_settlement(self.district.id, "Старый порт")
        self.service.set_support(self.district.id, first.id, self.a.id, 4)
        self.service.set_support(self.district.id, second.id, self.a.id, 1)
        self.assertEqual(self.district.support_points(self.a.id), 5)

    def test_modifier_is_points_over_settlement_count(self):
        first = self.service.add_settlement(self.district.id, "Первый")
        self.service.add_settlement(self.district.id, "Второй")
        self.service.set_support(self.district.id, first.id, self.a.id, 5)
        self.assertEqual(self.service.support_modifier(self.district.id, self.a.id), 2.5)

    def test_settlement_pool_cannot_be_overspent(self):
        settlement = self.service.add_settlement(self.district.id, "Гаффинсвик-Сити")
        self.service.set_support(self.district.id, settlement.id, self.a.id, 4)
        with self.assertRaises(ValidationError) as ctx:
            self.service.set_support(self.district.id, settlement.id, self.b.id, 3)
        self.assertIn("2", str(ctx.exception))   # подсказка про остаток

    def test_zero_points_remove_the_party(self):
        settlement = self.service.add_settlement(self.district.id, "Гаффинсвик-Сити")
        self.service.set_support(self.district.id, settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, settlement.id, self.a.id, 0)
        self.assertEqual(settlement.support, {})

    def test_deleting_a_settlement_changes_the_modifier(self):
        first = self.service.add_settlement(self.district.id, "Первый")
        second = self.service.add_settlement(self.district.id, "Второй")
        self.service.set_support(self.district.id, first.id, self.a.id, 6)
        self.assertEqual(self.service.support_modifier(self.district.id, self.a.id), 3.0)
        self.service.delete_settlement(self.district.id, second.id)
        self.assertEqual(self.service.support_modifier(self.district.id, self.a.id), 6.0)

    def test_settlements_survive_restart(self):
        settlement = self.service.add_settlement(self.district.id, "Гаффинсвик-Сити")
        self.service.set_support(self.district.id, settlement.id, self.a.id, 4)
        again = ParlamentService(self.path)
        again.bootstrap()
        district = next(d for d in again.project.districts if d.id == self.district.id)
        self.assertEqual([s.name for s in district.settlements], ["Гаффинсвик-Сити"])
        self.assertEqual(district.support_points(self.a.id), 4)

    def test_negative_points_are_rejected(self):
        settlement = self.service.add_settlement(self.district.id, "Гаффинсвик-Сити")
        with self.assertRaises(ValidationError):
            self.service.set_support(self.district.id, settlement.id, self.a.id, -1)


class TestRollElection(ElectionTestCase):
    """Розыгрыш выборов сервисом: кто участвует и что попадает в состав."""

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Гаффинсвик центр"]

    def give_support(self, party, points, settlements=1):
        made = [self.service.add_settlement(self.district.id, f"НП {i + 1}")
                for i in range(settlements)]
        self.service.set_support(self.district.id, made[0].id, party.id, points)
        return made

    def test_only_parties_with_something_in_the_district_take_part(self):
        # Иначе каждая партия лезла бы в каждый округ, включая чужие.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"debate": 1}}},
                                   rng=random.Random(1))
        taking_part = set(self.conv.rolls[self.district.id])
        self.assertEqual(taking_part, {self.a.id, self.b.id})
        self.assertNotIn(self.c.id, taking_part)

    def test_support_reaches_the_roll(self):
        self.give_support(self.a, 5, settlements=2)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(2))
        self.assertEqual(self.conv.rolls[self.district.id][self.a.id].support, 2.5)

    def test_debate_and_agitation_reach_the_roll(self):
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: {"debate": -3, "agitation": True}}},
            rng=random.Random(5))
        roll = self.conv.rolls[self.district.id][self.a.id]
        self.assertEqual(roll.debate, -3)
        self.assertTrue(roll.agitation)

    def test_seats_come_from_the_rolled_weights(self):
        self.give_support(self.a, 6)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"debate": 2}}},
                                   rng=random.Random(7))
        self.assertEqual(sum(self.conv.seats.values()), self.district.seats)

    def test_shares_add_up_to_a_hundred(self):
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"debate": 1}}},
                                   rng=random.Random(9))
        self.assertAlmostEqual(
            sum(self.service.district_shares(self.conv.id, self.district.id).values()),
            100.0, places=6)

    def test_same_seed_gives_the_same_result(self):
        self.give_support(self.a, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(11))
        first = dict(self.conv.seats)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(11))
        self.assertEqual(self.conv.seats, first)

    def test_rolls_are_stored_for_the_record(self):
        # Бросок случаен и не повторится: без сохранённой разбивки потом не
        # понять, из чего сложился результат.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(13))
        again = ParlamentService(self.path)
        again.bootstrap()
        stored = again.project.active_convocation.rolls[self.district.id][self.a.id]
        self.assertEqual(stored, self.conv.rolls[self.district.id][self.a.id])

    def test_empty_setup_leaves_the_parliament_empty(self):
        self.service.roll_election(self.conv.id, {}, rng=random.Random(17))
        self.assertEqual(self.conv.seats, {})
        self.assertFalse(self.conv.has_election)

    def test_clearing_wipes_the_rolls_too(self):
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(19))
        self.service.clear_election(self.conv.id)
        self.assertEqual(self.conv.rolls, {})
        self.assertEqual(self.conv.votes, {})

    def test_bad_debate_bonus_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(
                self.conv.id, {self.district.id: {self.a.id: {"debate": "ой"}}})


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
