"""Тесты выборов по округам: бросок, деление мест, победители, таблица поддержки."""

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
    SETTLEMENT_SUPPORT,
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
from parlament.ui.support_file import (  # noqa: E402
    export_support_template,
    parse_support_text,
)


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


class TestElectionResults(ElectionTestCase):
    """Что розыгрыш делает с составом созыва и с картой."""

    def only_party_here(self, district_name: str, party):
        """Даёт партии единственную поддержку в округе — округ уходит ей.

        Бросок случаен, поэтому «кто победит» задаётся не силой модификаторов,
        а тем, что соперников в округе нет: борьбы не было — победитель
        забирает всё.
        """
        district = self.by_name[district_name]
        settlement = self.service.add_settlement(district.id, f"НП {district.code}")
        self.service.set_support(district.id, settlement.id, party.id, 3)
        return district

    def test_results_fill_the_districts_that_were_contested(self):
        first = self.only_party_here("Гаффинсвик центр", self.a)
        second = self.only_party_here("Саттмалвик центр", self.b)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(3))
        self.assertEqual(self.conv.seats,
                         {self.a.id: first.seats, self.b.id: second.seats})
        self.assertTrue(self.conv.has_election)

    def test_winners_drive_the_map(self):
        big = self.only_party_here("Гаффинсвик центр", self.a)
        small = self.only_party_here("Судбригг", self.c)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(4))
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(winners[big.id], self.a.id)
        self.assertEqual(winners[small.id], self.c.id)

    def test_districts_without_results_stay_uncoloured(self):
        district = self.only_party_here("Судбригг", self.a)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(5))
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(list(winners), [district.id])

    def test_rerunning_replaces_previous_results(self):
        # Выборы — это результат целиком, а не точечная правка: округа, где
        # в новый розыгрыш никто не пошёл, обнуляются.
        district = self.only_party_here("Судбригг", self.a)
        self.service.roll_election(
            self.conv.id, {district.id: {self.a.id: {"agitation": True}},
                           self.by_name["Гаффинсвик центр"].id:
                               {self.b.id: {"agitation": True}}},
            rng=random.Random(6))
        self.assertEqual(set(self.conv.seats), {self.a.id, self.b.id})

        self.service.roll_election(self.conv.id, {}, rng=random.Random(6))
        self.assertEqual(self.conv.seats, {self.a.id: district.seats})

    def test_never_exceeds_the_parliament(self):
        # Даже если разыграть все округа, сумма ровно равна размеру палаты.
        setup = {d.id: {self.a.id: {"agitation": True},
                        self.b.id: {"debate": 2}}
                 for d in self.service.project.districts}
        self.service.roll_election(self.conv.id, setup, rng=random.Random(8))
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_results_survive_restart(self):
        self.only_party_here("Гаффинсвик центр", self.a)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(10))
        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(again.project.active_convocation.seats, self.conv.seats)
        self.assertTrue(again.project.active_convocation.has_election)

    def test_unknown_district_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id,
                                       {"нет-такого": {self.a.id: {"debate": 1}}})



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

    def test_empty_setup_is_refused(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, rng=random.Random(17))
        self.assertEqual(self.conv.seats, {})
        self.assertFalse(self.conv.has_election)

    def test_empty_roll_does_not_wipe_the_previous_one(self):
        # Кнопка, нажатая по ошибке, не должна стирать сыгранные выборы —
        # для этого есть отдельный сброс с подтверждением.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(21))
        before = dict(self.conv.seats)
        rolls_before = dict(self.conv.rolls)

        self.service.delete_settlement(self.district.id,
                                       self.district.settlements[0].id)
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, rng=random.Random(22))

        self.assertEqual(self.conv.seats, before)
        self.assertEqual(self.conv.rolls, rolls_before)

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


class TestDeletingAParty(ElectionTestCase):
    """Удаление партии не должно оставлять за ней следов."""

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Гаффинсвик центр"]
        self.settlement = self.service.add_settlement(self.district.id, "Гавань")

    def test_its_support_points_go_back_into_the_pool(self):
        # Запас пункта общий: очки исчезнувшей партии навсегда заняли бы
        # часть запаса, и отдать их другой партии стало бы нельзя.
        self.service.set_support(self.district.id, self.settlement.id,
                                 self.a.id, SETTLEMENT_SUPPORT)
        self.service.delete_party(self.a.id)
        self.service.set_support(self.district.id, self.settlement.id,
                                 self.b.id, SETTLEMENT_SUPPORT)
        self.assertEqual(self.settlement.support, {self.b.id: SETTLEMENT_SUPPORT})

    def test_it_leaves_the_rolled_districts(self):
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, self.settlement.id, self.b.id, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(31))
        self.assertEqual(set(self.conv.rolls[self.district.id]), {self.a.id, self.b.id})

        self.service.delete_party(self.a.id)
        self.assertEqual(set(self.conv.rolls[self.district.id]), {self.b.id})
        self.assertNotIn(self.a.id, self.conv.votes[self.district.id])

    def test_the_district_is_shared_out_again(self):
        # Иначе места ушедшей партии просто пропали бы, а округ остался бы
        # недоразделённым.
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, self.settlement.id, self.b.id, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(33))
        self.service.delete_party(self.a.id)
        self.assertEqual(sum(self.conv.seats.values()), self.district.seats)
        self.assertEqual(self.conv.seats, {self.b.id: self.district.seats})

    def test_deleting_the_last_party_clears_the_election(self):
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(35))
        self.service.delete_party(self.a.id)
        self.assertEqual(self.conv.seats, {})
        self.assertEqual(self.conv.votes, {})
        self.assertFalse(self.conv.has_election)

    def test_survives_restart(self):
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 5)
        self.service.delete_party(self.a.id)
        again = ParlamentService(self.path)
        again.bootstrap()
        stored = again.project.districts
        self.assertEqual(
            [s.support for d in stored for s in d.settlements], [{}])


class TestSupportImport(ElectionTestCase):
    """Таблица поддержки: населённые пункты и очки одним файлом.

    Пунктов много — по нескольку в каждом из 27 округов, — и заводить их
    руками долго; это ровно та работа, которую удобнее делать в таблице.
    """

    def parse(self, text: str):
        return parse_support_text(text.encode("utf-8"), self.service.project)

    def table(self, body: str) -> str:
        return ("Округ,Населённый пункт,Народный союз,Партия труда,Аграрный блок\n"
                + body)

    def test_creates_settlements_and_points(self):
        result = self.parse(self.table("Судбригг,Судурей,4,2,\n"
                                       "Судбригг,Фьярей,,6,\n"))
        self.assertEqual(result.warnings, [])
        self.assertEqual(self.service.import_support(result.rows), 2)

        district = self.by_name["Судбригг"]
        self.assertEqual([s.name for s in district.settlements], ["Судурей", "Фьярей"])
        self.assertEqual(district.support_points(self.b.id), 8)

    def test_modifier_follows_the_imported_table(self):
        result = self.parse(self.table("Судбригг,Судурей,4,,\n"
                                       "Судбригг,Фьярей,2,,\n"))
        self.service.import_support(result.rows)
        # 6 очков на два пункта.
        self.assertEqual(self.service.support_modifier(self.by_name["Судбригг"].id,
                                                       self.a.id), 3.0)

    def test_existing_settlement_is_replaced_not_doubled(self):
        district = self.by_name["Судбригг"]
        settlement = self.service.add_settlement(district.id, "Судурей")
        self.service.set_support(district.id, settlement.id, self.a.id, 5)

        result = self.parse(self.table("Судбригг,Судурей,,3,\n"))
        self.service.import_support(result.rows)

        self.assertEqual(len(district.settlements), 1)
        self.assertEqual(district.settlements[0].support, {self.b.id: 3})

    def test_names_match_loosely(self):
        result = self.parse("Округ,Населённый пункт,  народный СОЮЗ \n"
                            "  судбригг ,Судурей,3\n")
        self.assertEqual(result.warnings, [])
        self.service.import_support(result.rows)
        self.assertEqual(self.by_name["Судбригг"].support_points(self.a.id), 3)

    def test_overspent_settlement_is_reported_and_skipped(self):
        # Разбор ловит перебор сам, чтобы пользователь увидел разом все
        # плохие строки и правил документ за один заход.
        result = self.parse(self.table("Судбригг,Судурей,5,4,\n"
                                       "Гаффинсвик центр,Гавань,3,2,\n"))
        self.assertTrue(any("Судурей" in w and "6" in w for w in result.warnings))
        self.assertEqual(list(result.rows), [self.by_name["Гаффинсвик центр"].id])

        self.service.import_support(result.rows)
        self.assertEqual(self.by_name["Судбригг"].settlements, [])
        self.assertEqual(self.by_name["Гаффинсвик центр"].support_points(self.a.id), 3)

    def test_service_still_refuses_an_overspent_row(self):
        # Последний рубеж: разбор можно обойти, проект — нет.
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_support(
                {self.by_name["Судбригг"].id: {"Судурей": {self.a.id: 9}}})
        self.assertIn("Судурей", str(ctx.exception))

    def test_the_same_settlement_twice_is_reported(self):
        result = self.parse(self.table("Судбригг,Судурей,3,,\n"
                                       "Судбригг,Судурей,,2,\n"))
        self.assertTrue(any("дважды" in w for w in result.warnings))
        self.service.import_support(result.rows)
        self.assertEqual(len(self.by_name["Судбригг"].settlements), 1)
        self.assertEqual(self.by_name["Судбригг"].support_points(self.b.id), 2)

    def test_unknown_district_is_reported(self):
        result = self.parse(self.table("Атлантида,Столица,1,2,\n"))
        self.assertEqual(result.rows, {})
        self.assertTrue(any("Атлантида" in w for w in result.warnings))

    def test_row_with_points_but_no_settlement_name_is_reported(self):
        result = self.parse(self.table("Судбригг,,1,2,\n"))
        self.assertTrue(any("названия" in w for w in result.warnings))

    def test_blank_template_row_is_not_an_error(self):
        # Шаблон выдаёт пустую строку там, где пунктов ещё нет: это
        # приглашение заполнить, а не повод для замечания.
        result = self.parse(self.table("Судбригг,,,,\n"))
        self.assertEqual(result.warnings, ["Ни одного населённого пункта "
                                           "разобрать не удалось."])

    def test_a_bad_row_cancels_the_whole_import(self):
        # Иначе половина таблицы оседала бы в памяти, а в файл не попадала:
        # на экране одно, в проекте другое.
        first = self.by_name["Судбригг"]
        second = self.by_name["Гаффинсвик центр"]
        self.service.import_support({first.id: {"Судурей": {self.a.id: 3}}})

        with self.assertRaises(ValidationError):
            self.service.import_support({
                first.id: {"Судурей": {self.a.id: 1}},
                second.id: {"Гавань": {self.a.id: SETTLEMENT_SUPPORT + 1}},
            })

        self.assertEqual(first.settlements[0].support, {self.a.id: 3})
        self.assertEqual(second.settlements, [])
        again = ParlamentService(self.path)
        again.bootstrap()
        stored = {d.name: d for d in again.project.districts}
        self.assertEqual(stored["Судбригг"].settlements[0].support, {self.a.id: 3})
        self.assertEqual(stored["Гаффинсвик центр"].settlements, [])

    def test_template_lists_every_district(self):
        data = export_support_template(self.service.project).decode("utf-8-sig")
        lines = [line for line in data.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1 + len(self.service.project.districts))
        self.assertIn("Населённый пункт", lines[0])

    def test_template_carries_existing_settlements(self):
        district = self.by_name["Судбригг"]
        settlement = self.service.add_settlement(district.id, "Судурей")
        self.service.set_support(district.id, settlement.id, self.a.id, 4)

        data = export_support_template(self.service.project).decode("utf-8-sig")
        self.assertIn("Судбригг,Судурей,4", data)

    def test_template_round_trip(self):
        district = self.by_name["Судбригг"]
        settlement = self.service.add_settlement(district.id, "Судурей")
        self.service.set_support(district.id, settlement.id, self.a.id, 4)

        data = export_support_template(self.service.project)
        result = parse_support_text(data, self.service.project)
        self.assertEqual(result.warnings, [])
        self.service.import_support(result.rows)
        self.assertEqual(district.settlements[0].support, {self.a.id: 4})


if __name__ == "__main__":
    unittest.main()
