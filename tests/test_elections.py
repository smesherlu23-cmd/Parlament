"""Тесты выборов по округам: бросок, деление мест, победители, таблица поддержки."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_seed import SEED_DISTRICTS, SEED_TOTAL_SEATS, is_city  # noqa: E402
from parlament.elections import (  # noqa: E402
    CITY_SUPPORT,
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
from parlament.district_geometry import (  # noqa: E402
    DISTRICT_CENTRES,
    DISTRICT_SHAPES,
)
from parlament.model import Project  # noqa: E402
from parlament.ui.support_file import (  # noqa: E402
    _HELP_LINES,
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

    def test_modifier_can_be_any_number(self):
        self.assertEqual(PartyRoll(roll=5, modifier=4).total, 9.0)
        self.assertEqual(PartyRoll(roll=5, modifier=-3).total, 2.0)

    def test_all_modifiers_stack(self):
        self.assertAlmostEqual(
            PartyRoll(roll=4, support=7 / 3, modifier=-2).total,
            4 + 7 / 3 - 2)

    def test_total_never_goes_below_zero(self):
        # Отрицательный вес вычитал бы голоса у соседей и ломал пропорцию.
        self.assertEqual(PartyRoll(roll=1, modifier=-9).total, 0.0)
        self.assertEqual(PartyRoll(roll=2, support=-5).total, 0.0)

    def test_party_at_zero_gets_no_votes(self):
        rolls = {"a": PartyRoll(roll=6), "b": PartyRoll(roll=1, modifier=-5)}
        self.assertEqual(set(weights(rolls)), {"a"})
        self.assertEqual(shares(rolls), {"a": 100.0})

    def test_shares_always_add_up_to_a_hundred(self):
        rng = random.Random(7)
        for _ in range(200):
            rolls = {f"p{i}": PartyRoll(roll=roll_dice(rng), support=rng.random() * 4,
                                        modifier=rng.randint(-3, 3))
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
        original = PartyRoll(roll=6, support=2.5, modifier=-1)
        self.assertEqual(PartyRoll.from_dict(original.to_dict()), original)

    def test_old_files_still_load_the_modifier_under_its_old_name(self):
        # Раньше поле называлось "debate" — тем же значением ещё не
        # перезаписанные старые файлы не должны обнулиться.
        self.assertEqual(
            PartyRoll.from_dict({"roll": 5, "support": 0, "debate": 3}),
            PartyRoll(roll=5, support=0, modifier=3))

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

    def test_islands_stay_contiguous_in_seed_order(self):
        # Экраны группируют округа по острову в порядке списка; разбей
        # Виделлиус Судбриггом посередине — и «Остров Виделлиус» всплыл бы
        # в заголовках дважды с «Остров Нурик» между ними.
        from parlament.district_seed import island_of

        seen: list[str] = []
        for district in self.service.project.districts:
            island = island_of(district.region)
            if island not in seen:
                seen.append(island)
        self.assertEqual(len(seen), len(set(seen)),
                         f"остров повторился в списке: {seen}")

    def test_parliament_size_equals_sum_of_districts(self):
        # Карта — источник истины: разъехаться эти числа не должны.
        self.assertEqual(sum(d.seats for d in self.service.project.districts),
                         self.service.project.total_seats)

    def test_every_district_has_geometry(self):
        for district in self.service.project.districts:
            self.assertIn(district.code, DISTRICT_SHAPES, district.name)
            self.assertIn(district.code, DISTRICT_CENTRES, district.name)

    def test_labels_sit_inside_their_own_district(self):
        # Центр тяжести у вогнутых округов оказывался снаружи, и подпись
        # ложилась поверх соседа — «4» читалась на третьем округе.
        for district in self.service.project.districts:
            x, y = DISTRICT_CENTRES[district.code]
            own = DISTRICT_SHAPES[district.code]
            self.assertTrue(any(_inside(x, y, poly) for poly in own),
                            f"{district.name}: подпись вне округа")
            for code, polys in DISTRICT_SHAPES.items():
                if code == district.code:
                    continue
                self.assertFalse(any(_inside(x, y, poly) for poly in polys),
                                 f"{district.name}: подпись легла на округ {code}")

    def test_old_projects_keep_their_own_size(self):
        # Проект, созданный до карты, не должен внезапно получить округа и
        # чужой размер парламента — там места набраны руками.
        old = Project.from_dict({"schemaVersion": 1, "totalSeats": 120,
                                 "parties": [], "convocations": []})
        self.assertEqual(old.total_seats, 120)
        self.assertEqual(old.districts, [])


class TestElectionResults(ElectionTestCase):
    """Что розыгрыш делает с составом созыва и с картой."""

    def setUp(self):
        super().setUp()
        #: `{district_id: {party_id: {"modifier": ...}}}` — подавляющий
        #: перевес, зарегистрированный через `only_party_here`. Передаётся в
        #: `roll_election` вместо пустого набора модификаторов.
        self.dominance: dict[str, dict[str, dict]] = {}

    def only_party_here(self, district_name: str, party):
        """Даёт партии подавляющий перевес в округе — округ уходит ей
        гарантированно, а не по случайности броска.

        Раньше единственная поддержка была гарантией сама по себе: соперники
        вообще не участвовали в розыгрыше. Теперь бросают все, и обычной
        поддержки (до 6 очков на сельский пункт) мало, чтобы обыграть чужой
        бросок 1–10 в худшем случае — поэтому рядом с реальной поддержкой
        регистрируется огромный модификатор в `self.dominance`, который
        соперники не могут перекрыть уже никаким броском.
        """
        district = self.by_name[district_name]
        if is_city(district.code):
            city = self.service.project.city(district.region)
            self.service.set_city_support(city.id, party.id, 3)
        else:
            settlement = self.service.add_settlement(district.id, f"НП {district.code}")
            self.service.set_support(district.id, settlement.id, party.id, 3)
        self.dominance.setdefault(district.id, {})[party.id] = {"modifier": 1000}
        return district

    def test_results_fill_the_districts_that_were_contested(self):
        first = self.only_party_here("Херсвикский", self.a)
        second = self.only_party_here("Холмавикский", self.b)
        self.service.roll_election(self.conv.id, self.dominance, rng=random.Random(3))
        allocation = self.service.district_allocation(self.conv.id)
        self.assertEqual(allocation[first.id], {self.a.id: first.seats})
        self.assertEqual(allocation[second.id], {self.b.id: second.seats})
        self.assertTrue(self.conv.has_election)

    def test_winners_drive_the_map(self):
        big = self.only_party_here("Херсвикский", self.a)
        small = self.only_party_here("Судбригг", self.c)
        self.service.roll_election(self.conv.id, self.dominance, rng=random.Random(4))
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(winners[big.id], self.a.id)
        self.assertEqual(winners[small.id], self.c.id)

    def test_city_support_wins_every_district_of_that_city(self):
        # Копилка общая на весь город: поддержка в ней достаётся сразу всем
        # его избирательным округам, а не одному конкретному району. Берём
        # весь запас города (12): при делителе 1 это больше, чем может дать
        # чужой голый бросок (максимум 10) — победа гарантирована безо
        # всякого искусственного перевеса, только настоящей поддержкой.
        gaffinsvik = self.by_name["Гаффинсвик центр"]
        city = self.service.project.city(gaffinsvik.region)
        self.service.set_city_support(city.id, self.a.id, CITY_SUPPORT)
        city_districts = [d for d in self.service.project.districts
                          if is_city(d.code) and d.region == gaffinsvik.region]
        self.assertEqual(len(city_districts), 5)

        self.service.roll_election(self.conv.id, {}, rng=random.Random(7))
        winners = self.service.district_winners(self.conv.id)
        for district in city_districts:
            self.assertEqual(winners[district.id], self.a.id, district.name)
        allocation = self.service.district_allocation(self.conv.id)
        self.assertEqual(
            sum(sum(allocation.get(d.id, {}).values()) for d in city_districts),
            sum(d.seats for d in city_districts))

    def test_every_district_gets_a_winner(self):
        # Раньше округ без ставок оставался серым — участников там не было.
        # Теперь бросают все, поэтому карта раскрашивается целиком, даже
        # там, где поддержки никто не выставлял.
        district = self.only_party_here("Судбригг", self.a)
        self.service.roll_election(self.conv.id, self.dominance, rng=random.Random(5))
        winners = self.service.district_winners(self.conv.id)
        self.assertEqual(winners[district.id], self.a.id)
        self.assertEqual(len(winners), len(self.service.project.districts))

    def test_rerunning_replaces_previous_results(self):
        # Выборы — это результат целиком, а не точечная правка: второй
        # розыгрыш переписывает разбор округа заново, а не донакладывает
        # его на прошлый.
        district = self.only_party_here("Судбригг", self.a)
        self.service.roll_election(self.conv.id, self.dominance, rng=random.Random(6))
        self.assertEqual(self.service.district_winners(self.conv.id)[district.id], self.a.id)
        self.assertEqual(self.conv.rolls[district.id][self.a.id].modifier, 1000)

        self.service.roll_election(self.conv.id, {}, rng=random.Random(6))
        self.assertEqual(self.conv.rolls[district.id][self.a.id].modifier, 0)

    def test_never_exceeds_the_parliament(self):
        # Даже если разыграть все округа, сумма ровно равна размеру палаты.
        setup = {d.id: {self.a.id: {"modifier": 1},
                        self.b.id: {"modifier": 2}}
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
                                       {"нет-такого": {self.a.id: {"modifier": 1}}})



class TestSettlementsAndSupport(ElectionTestCase):
    """Населённые пункты и очки неформальной популярности."""

    def setUp(self):
        super().setUp()
        #: Сельский округ: четыре пункта с карты.
        self.village = self.by_name["Западный берег"]
        #: Городской округ и его общая копилка: сам округ пунктов не хранит.
        self.city_district = self.by_name["Гаффинсвик центр"]
        self.city = self.service.project.city(self.city_district.region)

    def test_districts_come_with_their_settlements(self):
        # Пункты есть на карте и все известны: заставлять вбивать их руками
        # в двадцати семи округах — работа на пустом месте.
        self.assertEqual([s.name for s in self.village.settlements],
                         ["Сандавик", "Саттмалахолл", "Вестурфьорд", "Нордурнес"])

    def test_a_city_district_has_no_settlements_of_its_own(self):
        # Его очки — не свои, а общие на весь город (`Project.cities`).
        self.assertEqual(self.city_district.settlements, [])

    def test_the_city_pool_exists_and_holds_twelve(self):
        self.assertIsNotNone(self.city)
        self.assertEqual(self.city.name, self.city_district.region)
        self.assertEqual(self.city.capacity, CITY_SUPPORT)

    def test_a_village_settlement_holds_six(self):
        for settlement in self.village.settlements:
            self.assertEqual(settlement.capacity, SETTLEMENT_SUPPORT)

    def test_points_add_up_across_settlements(self):
        first, second = self.village.settlements[:2]
        self.service.set_support(self.village.id, first.id, self.a.id, 4)
        self.service.set_support(self.village.id, second.id, self.a.id, 1)
        self.assertEqual(self.village.support_points(self.a.id), 5)

    def test_modifier_is_points_over_settlement_count(self):
        first = self.village.settlements[0]
        self.service.set_support(self.village.id, first.id, self.a.id, 6)
        # Шесть очков на четыре пункта округа.
        self.assertEqual(self.service.support_modifier(self.village.id, self.a.id), 1.5)

    def test_settlement_pool_cannot_be_overspent(self):
        settlement = self.village.settlements[0]
        self.service.set_support(self.village.id, settlement.id, self.a.id, 4)
        with self.assertRaises(ValidationError) as ctx:
            self.service.set_support(self.village.id, settlement.id, self.b.id, 3)
        self.assertIn("2", str(ctx.exception))   # подсказка про остаток

    def test_a_city_pool_is_twice_as_big(self):
        self.service.set_city_support(self.city.id, self.a.id, 7)
        self.service.set_city_support(self.city.id, self.b.id, 5)
        self.assertEqual(sum(self.city.support.values()), CITY_SUPPORT)
        with self.assertRaises(ValidationError):
            self.service.set_city_support(self.city.id, self.c.id, 1)

    def test_the_city_pool_is_shared_by_all_its_districts(self):
        # Саттмалвик-порт и -центр делят один и тот же запас: очки,
        # выставленные через любой из них, видны у обоих.
        port = self.by_name["Саттмалвик порт"]
        centre = self.by_name["Саттмалвик центр"]
        self.assertEqual(port.region, centre.region)
        city = self.service.project.city(port.region)

        self.service.set_city_support(city.id, self.a.id, 6)
        self.assertEqual(
            self.service.support_modifier(port.id, self.a.id),
            self.service.support_modifier(centre.id, self.a.id))
        self.assertEqual(self.service.support_modifier(port.id, self.a.id), 6.0)

    def test_zero_points_remove_the_party(self):
        settlement = self.village.settlements[0]
        self.service.set_support(self.village.id, settlement.id, self.a.id, 3)
        self.service.set_support(self.village.id, settlement.id, self.a.id, 0)
        self.assertEqual(settlement.support, {})

    def test_deleting_a_settlement_changes_the_modifier(self):
        first = self.village.settlements[0]
        self.service.set_support(self.village.id, first.id, self.a.id, 6)
        self.assertEqual(self.service.support_modifier(self.village.id, self.a.id), 1.5)
        for extra in list(self.village.settlements[1:]):
            self.service.delete_settlement(self.village.id, extra.id)
        self.assertEqual(self.service.support_modifier(self.village.id, self.a.id), 6.0)

    def test_an_extra_settlement_can_be_added(self):
        # Хутора, которого нет на карте, программа не запрещает.
        made = self.service.add_settlement(self.village.id, "Новый хутор")
        self.assertEqual(made.capacity, SETTLEMENT_SUPPORT)
        self.assertEqual(len(self.village.settlements), 5)

    def test_a_city_district_refuses_settlements(self):
        with self.assertRaises(ValidationError):
            self.service.add_settlement(self.city_district.id, "Пригород")

    def test_settlements_survive_restart(self):
        settlement = self.village.settlements[0]
        self.service.set_support(self.village.id, settlement.id, self.a.id, 4)
        again = ParlamentService(self.path)
        again.bootstrap()
        district = next(d for d in again.project.districts if d.id == self.village.id)
        self.assertEqual([s.name for s in district.settlements],
                         [s.name for s in self.village.settlements])
        self.assertEqual(district.support_points(self.a.id), 4)

    def test_city_points_survive_restart(self):
        self.service.set_city_support(self.city.id, self.a.id, 5)
        again = ParlamentService(self.path)
        again.bootstrap()
        city = again.project.city(self.city_district.region)
        self.assertEqual(city.support, {self.a.id: 5})
        self.assertEqual(city.capacity, CITY_SUPPORT)

    def test_negative_points_are_rejected(self):
        settlement = self.village.settlements[0]
        with self.assertRaises(ValidationError):
            self.service.set_support(self.village.id, settlement.id, self.a.id, -1)

    def test_a_deleted_village_stays_deleted_after_restart(self):
        # Пункт с карты и «пункт, которого сейчас нет», отличаются только
        # решением игрока — при следующем открытии файла карта не должна
        # тихо возвращать удалённое обратно.
        settlement = self.village.settlements[0]
        self.service.delete_settlement(self.village.id, settlement.id)
        again = ParlamentService(self.path)
        again.bootstrap()
        village = next(d for d in again.project.districts if d.id == self.village.id)
        self.assertEqual(len(village.settlements), 3)
        self.assertNotIn(settlement.name, [s.name for s in village.settlements])

    def test_a_renamed_village_does_not_get_duplicated_after_restart(self):
        settlement = self.village.settlements[0]
        old_name = settlement.name
        self.service.rename_settlement(self.village.id, settlement.id, "Новое имя")
        again = ParlamentService(self.path)
        again.bootstrap()
        village = next(d for d in again.project.districts if d.id == self.village.id)
        names = [s.name for s in village.settlements]
        self.assertEqual(len(names), 4)
        self.assertIn("Новое имя", names)
        self.assertNotIn(old_name, names)

    def test_an_old_project_without_settlements_gets_migrated_once(self):
        # Проекты, начатые до появления пунктов на карте, дописываются один
        # раз — а не при каждом открытии файла.
        import json

        from parlament import store
        from parlament.model import new_id

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw["districts"]:
            if item["id"] == self.village.id:
                item["settlements"] = []
                item.pop("settlementsSynced", None)   # старый файл флага не знал
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        migrated = store.load(self.path)
        village = next(d for d in migrated.districts if d.id == self.village.id)
        self.assertEqual(len(village.settlements), 4)
        self.assertTrue(village.settlements_synced)

        # Удаляем один пункт и убеждаемся, что повторная миграция не идёт.
        removed = village.settlements[0]
        village.settlements = village.settlements[1:]
        store.save(migrated, self.path)
        again = store.load(self.path)
        village_again = next(d for d in again.districts if d.id == self.village.id)
        self.assertEqual(len(village_again.settlements), 3)
        self.assertNotIn(removed.name, [s.name for s in village_again.settlements])


class TestRollElection(ElectionTestCase):
    """Розыгрыш выборов сервисом: кто участвует и что попадает в состав."""

    def setUp(self):
        super().setUp()
        #: Судбригг — два населённых пункта, удобно считать средние.
        self.district = self.by_name["Судбригг"]

    def give_support(self, party, points, where: int = 0):
        """Раздаёт очки в одном из пунктов округа — прямо как на экране."""
        settlement = self.district.settlements[where]
        self.service.set_support(self.district.id, settlement.id, party.id, points)
        return settlement

    def test_every_party_takes_part_regardless_of_support(self):
        # Раньше в округ лезли только партии с поддержкой или модификатором —
        # это и была ошибка: бросают все, а поддержка лишь прибавляется.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"modifier": 1}}},
                                   rng=random.Random(1))
        taking_part = set(self.conv.rolls[self.district.id])
        self.assertEqual(taking_part, {self.a.id, self.b.id, self.c.id})

    def test_support_reaches_the_roll(self):
        # Пять очков на два пункта округа — модификатор 2,5.
        self.give_support(self.a, 5)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(2))
        self.assertEqual(self.conv.rolls[self.district.id][self.a.id].support, 2.5)

    def test_modifier_reaches_the_roll(self):
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: {"modifier": -3}}},
            rng=random.Random(5))
        roll = self.conv.rolls[self.district.id][self.a.id]
        self.assertEqual(roll.modifier, -3)

    def test_seats_come_from_the_rolled_weights(self):
        self.give_support(self.a, 6)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"modifier": 2}}},
                                   rng=random.Random(7))
        # Бросают все партии во всех округах — палата набирается целиком.
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_shares_add_up_to_a_hundred(self):
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"modifier": 1}}},
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

    def test_empty_setup_still_fills_the_whole_parliament(self):
        # Пустая настройка — не «нечего разыгрывать»: без поддержки и
        # модификаторов все партии всё равно бросают голый кубик.
        self.service.roll_election(self.conv.id, {}, rng=random.Random(17))
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)
        self.assertTrue(self.conv.has_election)

    def test_no_districts_is_refused(self):
        empty = ParlamentService(Path(self._dir.name) / "без-округов.parlament.json")
        empty.bootstrap()
        empty.project.districts.clear()
        conv = empty.project.active_convocation
        empty.create_party(name="Народный союз", color="#0088b0")
        with self.assertRaises(ValidationError):
            empty.roll_election(conv.id, {})

    def test_no_parties_is_refused(self):
        empty = ParlamentService(Path(self._dir.name) / "без-партий.parlament.json")
        empty.bootstrap()
        conv = empty.project.active_convocation
        with self.assertRaises(ValidationError):
            empty.roll_election(conv.id, {})

    def test_rerolling_replaces_the_previous_result(self):
        # Повторный розыгрыш — это новый результат целиком, а не добавка к
        # старому: даже без штрафов и поддержки он переписывает прошлый.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(21))
        before = dict(self.conv.rolls)

        self.service.roll_election(self.conv.id, {}, rng=random.Random(22))
        self.assertNotEqual(self.conv.rolls, before)
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_clearing_wipes_the_rolls_too(self):
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(19))
        self.service.clear_election(self.conv.id)
        self.assertEqual(self.conv.rolls, {})
        self.assertEqual(self.conv.votes, {})

    def test_bad_modifier_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(
                self.conv.id, {self.district.id: {self.a.id: {"modifier": "ой"}}})


class TestElectionAndManualSeatsDoNotMix(ElectionTestCase):
    """Состав, посчитанный выборами, руками не правится."""

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Херсвикский"]
        settlement = self.service.add_settlement(self.district.id, "Гавань")
        self.service.set_support(self.district.id, settlement.id, self.a.id, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(41))

    def test_setting_seats_by_hand_is_refused(self):
        # Иначе зал разошёлся бы с картой: округа остались бы покрашены и
        # расписаны по партиям, а число мест в зале — уже другое.
        before = dict(self.conv.seats)
        with self.assertRaises(ValidationError):
            self.service.set_seats(self.conv.id, self.b.id, 5)
        self.assertEqual(self.conv.seats, before)

    def test_resetting_seats_is_refused(self):
        with self.assertRaises(ValidationError):
            self.service.reset_seats(self.conv.id)
        # Бросают все партии во всех округах — палата всегда набирается
        # целиком, вне зависимости от того, где расставлена поддержка.
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_after_clearing_the_election_hands_are_free_again(self):
        self.service.clear_election(self.conv.id)
        self.service.set_seats(self.conv.id, self.b.id, 5)
        self.assertEqual(self.conv.seats, {self.b.id: 5})


class TestEveryoneRolledZero(ElectionTestCase):
    """Штрафы увели всех в ноль: в округе голосов нет, но выборы состоялись.

    Раньше округ можно было обнулить, обнулив единственную участвующую
    партию — теперь бросают все, так что обнулить округ целиком можно,
    только дав штраф -20 (больше максимума броска) каждой партии.
    """

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Судбригг"]
        penalty = {"modifier": -20}
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: penalty, self.b.id: penalty,
                                self.c.id: penalty}},
            rng=random.Random(43))

    def test_the_roll_is_kept_for_the_record(self):
        self.assertEqual(self.conv.rolls[self.district.id][self.a.id].total, 0.0)
        self.assertNotIn(self.district.id, self.conv.votes)
        self.assertEqual(self.service.district_allocation(self.conv.id)
                         .get(self.district.id, {}), {})

    def test_it_still_counts_as_an_election(self):
        # Иначе программа предложила бы набрать места руками поверх уже
        # сыгранного розыгрыша.
        self.assertTrue(self.conv.has_election)
        with self.assertRaises(ValidationError):
            self.service.set_seats(self.conv.id, self.a.id, 2)

    def test_nobody_wins_the_district(self):
        self.assertNotIn(self.district.id, self.service.district_winners(self.conv.id))
        self.assertEqual(self.service.district_shares(self.conv.id, self.district.id), {})


class TestDeletingAParty(ElectionTestCase):
    """Удаление партии не должно оставлять за ней следов."""

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Херсвикский"]
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
        # Бросают все партии, включая ту, что без ставок (self.c) — она
        # остаётся в разборе округа и после удаления соседей.
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, self.settlement.id, self.b.id, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(31))
        self.assertEqual(set(self.conv.rolls[self.district.id]),
                         {self.a.id, self.b.id, self.c.id})

        self.service.delete_party(self.a.id)
        self.assertEqual(set(self.conv.rolls[self.district.id]), {self.b.id, self.c.id})
        self.assertNotIn(self.a.id, self.conv.votes.get(self.district.id, {}))

    def test_the_district_is_shared_out_again(self):
        # Иначе места ушедшей партии просто пропали бы, а округ остался бы
        # недоразделённым.
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, self.settlement.id, self.b.id, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(33))
        self.service.delete_party(self.a.id)
        # Бросают все партии во всех округах — палата набирается целиком, а
        # не только этим округом; здесь проверяем, что сам округ по-прежнему
        # полностью разделён и что a среди претендентов больше нет.
        allocation = self.service.district_allocation(self.conv.id)[self.district.id]
        self.assertEqual(sum(allocation.values()), self.district.seats)
        self.assertNotIn(self.a.id, allocation)
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_deleting_a_party_removes_only_its_own_roll(self):
        # У партии с одним лишь штрафом всё равно есть свой бросок в разборе
        # округа, даже если итог ушёл в ноль. Удаление партии стирает именно
        # её запись — другие партии в этом же округе остаются как были.
        self.service.roll_election(
            self.conv.id, {self.district.id: {self.a.id: {"modifier": -20}}},
            rng=random.Random(37))
        self.assertIn(self.a.id, self.conv.rolls[self.district.id])

        self.service.delete_party(self.a.id)
        self.assertNotIn(self.a.id, self.conv.rolls[self.district.id])
        self.assertIn(self.b.id, self.conv.rolls[self.district.id])

    def test_deleting_every_party_clears_the_election(self):
        # Пока остаётся хоть одна партия, она бросает кубик во всех округах,
        # и созыв не пустеет. Обнуляется он только когда партий не осталось.
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(35))
        self.assertTrue(self.conv.has_election)

        self.service.delete_party(self.a.id)
        self.service.delete_party(self.b.id)
        self.assertTrue(self.conv.has_election)

        self.service.delete_party(self.c.id)
        self.assertEqual(self.conv.seats, {})
        self.assertEqual(self.conv.votes, {})
        self.assertEqual(self.conv.rolls, {})
        self.assertFalse(self.conv.has_election)

    def test_survives_restart(self):
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 5)
        self.service.delete_party(self.a.id)
        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual([s.support for d in again.project.districts
                          for s in d.settlements if s.support], [])


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
        settlement = district.settlements[0]          # «Судурей» с карты
        self.service.set_support(district.id, settlement.id, self.a.id, 5)

        result = self.parse(self.table("Судбригг,Судурей,,3,\n"))
        self.service.import_support(result.rows)

        self.assertEqual(len(district.settlements), 2)
        self.assertEqual(settlement.support, {self.b.id: 3})

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
                                       "Северный мыс,Барвик,3,2,\n"))
        self.assertTrue(any("Судурей" in w and "6" in w for w in result.warnings))
        self.assertEqual(list(result.rows), [self.by_name["Северный мыс"].id])

        self.service.import_support(result.rows)
        self.assertEqual(self.by_name["Судбригг"].support_points(self.a.id), 0)
        self.assertEqual(self.by_name["Северный мыс"].support_points(self.a.id), 3)

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
        self.assertEqual(len(self.by_name["Судбригг"].settlements), 2)
        self.assertEqual(self.by_name["Судбригг"].support_points(self.b.id), 2)

    def test_a_city_row_may_hold_twice_as_much(self):
        result = self.parse(self.table("Гаффинсвик,Гаффинсвик,7,5,\n"))
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.rows, {})
        self.service.import_city_support(result.city_rows)

        gaffinsvik = self.by_name["Гаффинсвик центр"]
        city = self.service.project.city(gaffinsvik.region)
        self.assertEqual(city.support.get(self.a.id), 7)
        self.assertEqual(sum(city.support.values()), CITY_SUPPORT)
        # Модификатор одинаков на любом округе этого города.
        self.assertEqual(
            self.service.support_modifier(gaffinsvik.id, self.a.id),
            self.service.support_modifier(self.by_name["Гаффинсвик порт"].id, self.a.id))

    def test_a_city_row_can_be_matched_by_the_city_name_only(self):
        # «Гаффинсвик центр» (конкретный округ) в округ не годится — своих
        # очков у него нет, есть только у «Гаффинсвик» (города).
        result = self.parse(self.table("Гаффинсвик центр,Гаффинсвик центр,7,5,\n"))
        self.assertTrue(any("Гаффинсвик центр" in w and "не найден" in w
                            for w in result.warnings))
        self.assertEqual(result.city_rows, {})

    def test_a_city_row_can_be_overspent_too(self):
        result = self.parse(self.table("Гаффинсвик,Гаффинсвик,7,6,\n"))
        self.assertTrue(any("Гаффинсвик" in w and "13" in w for w in result.warnings))
        self.assertEqual(result.city_rows, {})

    def test_a_village_row_is_still_held_to_six(self):
        result = self.parse(self.table("Судбригг,Судурей,7,,\n"))
        self.assertTrue(any("их 6" in w for w in result.warnings))
        self.assertEqual(result.rows, {})

    def test_a_bad_cell_says_what_is_wrong(self):
        # «1,5» и «-2» — числа, просто очки бывают только целыми и от нуля:
        # писать «не число» было бы неправдой.
        result = self.parse(self.table("Судбригг,Судурей,абв,1,5,\n"))
        self.assertTrue(any("не подходит" in w and "целое" in w
                            for w in result.warnings))

        half = self.parse(self.table("Судбригг,Судурей,1.5,,\n"))
        self.assertTrue(any("не подходит" in w for w in half.warnings))

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
        second = self.by_name["Северный мыс"]
        self.service.import_support({first.id: {"Судурей": {self.a.id: 3}}})

        with self.assertRaises(ValidationError):
            self.service.import_support({
                first.id: {"Судурей": {self.a.id: 1}},
                second.id: {"Барвик": {self.a.id: SETTLEMENT_SUPPORT + 1}},
            })

        self.assertEqual(first.settlements[0].support, {self.a.id: 3})
        self.assertEqual(second.support_points(self.a.id), 0)
        again = ParlamentService(self.path)
        again.bootstrap()
        stored = {d.name: d for d in again.project.districts}
        self.assertEqual(stored["Судбригг"].settlements[0].support, {self.a.id: 3})
        self.assertEqual(stored["Северный мыс"].support_points(self.a.id), 0)

    def test_template_lists_every_settlement(self):
        data = export_support_template(self.service.project).decode("utf-8-sig")
        lines = [line for line in data.splitlines() if line.strip()]
        places = sum(len(d.settlements) for d in self.service.project.districts)
        cities = len(self.service.project.cities)
        help_lines = len(_HELP_LINES)
        self.assertEqual(len(lines), help_lines + 1 + places + cities)
        self.assertIn("Населённый пункт", lines[help_lines])
        self.assertIn("Судбригг,Судурей", data)
        # Города — по одной строке на метрополию, не на каждый её округ, и
        # без «населённого пункта» с тем же именем — это не отдельное село.
        self.assertIn("Гаффинсвик,,", data)
        self.assertNotIn("Гаффинсвик,Гаффинсвик", data)
        self.assertNotIn("Гаффинсвик центр,", data)
        self.assertNotIn("Гаффинсвик порт,", data)

    def test_template_carries_the_points_already_given(self):
        district = self.by_name["Судбригг"]
        settlement = district.settlements[0]
        self.service.set_support(district.id, settlement.id, self.a.id, 4)

        data = export_support_template(self.service.project).decode("utf-8-sig")
        self.assertIn("Судбригг,Судурей,4", data)

    def test_template_round_trip(self):
        district = self.by_name["Судбригг"]
        settlement = district.settlements[0]
        self.service.set_support(district.id, settlement.id, self.a.id, 4)
        gaffinsvik = self.by_name["Гаффинсвик центр"]
        city = self.service.project.city(gaffinsvik.region)
        self.service.set_city_support(city.id, self.b.id, 6)

        data = export_support_template(self.service.project)
        result = parse_support_text(data, self.service.project)
        self.assertEqual(result.warnings, [])
        self.service.import_support_table(result.rows, result.city_rows)
        self.assertEqual(district.settlements[0].support, {self.a.id: 4})
        self.assertEqual(city.support, {self.b.id: 6})

    def test_combined_import_is_all_or_nothing(self):
        # Ошибка в городской половине не должна оставлять уже принятую
        # сельскую половину в памяти, но не в файле.
        district = self.by_name["Судбригг"]
        gaffinsvik = self.by_name["Гаффинсвик центр"]
        city = self.service.project.city(gaffinsvik.region)

        with self.assertRaises(ValidationError):
            self.service.import_support_table(
                {district.id: {"Судурей": {self.a.id: 3}}},
                {city.id: {self.a.id: CITY_SUPPORT + 1}},
            )

        self.assertEqual(district.settlements[0].support, {})
        self.assertEqual(city.support, {})
        again = ParlamentService(self.path)
        again.bootstrap()
        stored_district = next(d for d in again.project.districts
                               if d.id == district.id)
        self.assertEqual(stored_district.settlements[0].support, {})

    def test_combined_import_applies_both_halves_together(self):
        district = self.by_name["Судбригг"]
        gaffinsvik = self.by_name["Гаффинсвик центр"]
        city = self.service.project.city(gaffinsvik.region)

        touched = self.service.import_support_table(
            {district.id: {"Судурей": {self.a.id: 3}}},
            {city.id: {self.b.id: 6}},
        )
        self.assertEqual(touched, 2)
        self.assertEqual(district.settlements[0].support, {self.a.id: 3})
        self.assertEqual(city.support, {self.b.id: 6})


def _inside(x: float, y: float, poly) -> bool:
    """Точка внутри многоугольника — та же трассировка луча, что и на карте."""
    inside = False
    count = len(poly)
    j = count - 1
    for i in range(count):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


if __name__ == "__main__":
    unittest.main()
