"""Тесты выборов по округам: доли, поправки, деление мест, победители, таблица поддержки."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_seed import (  # noqa: E402
    SEED_DISTRICTS,
    SEED_TOTAL_SEATS,
    is_city,
    island_of,
)
from parlament.elections import (  # noqa: E402
    CITY_SUPPORT,
    SETTLEMENT_SUPPORT,
    THRESHOLD_PERCENT,
    WOBBLE_RANGE,
    PartyResult,
    allocate_seats,
    base_shares,
    district_winner,
    normalize_shares,
    renormalize,
    roll_wobble,
    shares,
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
    """Розыгрыш голосов: база от очков, поправки, случайное колебание."""

    def test_example_from_the_brief(self):
        # Заказчик задал разбор на числах: 3, 5, 7 при сумме 15.
        results = {"a": PartyResult(share=20.0), "b": PartyResult(share=33.3),
                  "c": PartyResult(share=46.7)}
        percent = {k: round(v, 1) for k, v in shares(results).items()}
        self.assertEqual(percent, {"a": 20.0, "b": 33.3, "c": 46.7})

    def test_base_share_is_share_of_total_points_in_the_district(self):
        # Запас разобран целиком — доли ровно по очкам.
        self.assertEqual(base_shares({"a": 6, "b": 4}, 10), {"a": 60.0, "b": 40.0})
        self.assertAlmostEqual(base_shares({"a": 1, "b": 2}, 3)["a"], 33.333, places=2)

    def test_undistributed_points_are_shared_out_evenly(self):
        # Одно очко из шести на трёх партиях: перевес есть, разгрома нет.
        # Раньше это давало ровно те же 100 %, что и все шесть очков.
        result = base_shares({"a": 1, "b": 0, "c": 0}, 6)
        self.assertAlmostEqual(result["a"], (1 + 5 / 3) / 6 * 100)
        self.assertAlmostEqual(result["b"], (5 / 3) / 6 * 100)
        self.assertEqual(result["b"], result["c"])
        self.assertAlmostEqual(sum(result.values()), 100.0)

    def test_one_point_is_worth_less_than_the_whole_pool(self):
        # Главное свойство: чем больше разобрано, тем крупнее перевес.
        rising = [base_shares({"a": n, "b": 0, "c": 0}, 6)["a"] for n in range(7)]
        self.assertEqual(rising, sorted(rising))
        self.assertAlmostEqual(rising[0], 100 / 3)     # не раздали ничего
        self.assertAlmostEqual(rising[6], 100.0)       # разобрали целиком
        self.assertLess(rising[1], 50.0)               # одно очко — не разгром

    def test_a_full_pool_leaves_nobody_undecided(self):
        self.assertEqual(base_shares({"a": 6, "b": 0}, 6), {"a": 100.0, "b": 0.0})

    def test_more_points_given_out_beats_the_same_split_of_fewer(self):
        # Две партии поделили пункт поровну — но в одном случае разобрали
        # весь запас, в другом только треть. Доли выходят одинаковые: делить
        # между собой поровну и там и там, а неопределившиеся тоже поровну.
        self.assertEqual(base_shares({"a": 3, "b": 3}, 6),
                         base_shares({"a": 1, "b": 1}, 6))

    def test_capacity_smaller_than_what_was_given_falls_back(self):
        # Файл правили руками и раздали больше запаса: считаем от розданного,
        # чтобы доли не ушли за сотню.
        result = base_shares({"a": 8, "b": 2}, 6)
        self.assertEqual(result, {"a": 80.0, "b": 20.0})

    def test_without_a_known_capacity_it_counts_from_what_was_given(self):
        # Округ без пунктов — такое бывает у проектов, начатых до карты:
        # считать от запаса там не от чего.
        self.assertEqual(base_shares({"a": 3, "b": 1}, 0), {"a": 75.0, "b": 25.0})

    def test_base_share_ignores_negative_points(self):
        # Отрицательных очков не бывает по вводу, но на всякий случай не
        # даём им превратиться в отрицательную долю.
        self.assertEqual(base_shares({"a": -3, "b": 6}, 6), {"a": 0.0, "b": 100.0})

    def test_nobody_organized_means_everyone_is_equal(self):
        # Если очков не роздано вовсе, у организации нет ни у кого — все
        # партии равны, и голоса решит колебание, а не выдуманный перевес.
        equal = base_shares({"a": 0, "b": 0, "c": 0}, 6)
        self.assertEqual(set(equal), {"a", "b", "c"})
        for value in equal.values():
            self.assertAlmostEqual(value, 100 / 3)

    def test_base_shares_of_an_empty_district_is_empty(self):
        self.assertEqual(base_shares({}, 6), {})

    def test_wobble_stays_within_its_range(self):
        rng = random.Random(1)
        values = [roll_wobble(rng) for _ in range(2000)]
        self.assertGreaterEqual(min(values), -WOBBLE_RANGE)
        self.assertLessEqual(max(values), WOBBLE_RANGE)

    def test_wobble_is_not_uniform(self):
        # Реальные выборы редко оборачиваются сюрпризом: колебание должно
        # чаще выпадать рядом с нулём, чем у самых краёв диапазона.
        rng = random.Random(2)
        values = [roll_wobble(rng) for _ in range(5000)]
        middle = sum(1 for v in values if abs(v) < 0.5)
        edges = sum(1 for v in values if abs(v) > WOBBLE_RANGE - 0.5)
        self.assertGreater(middle, edges * 3)

    def test_wobble_stays_reproducible_for_the_same_seed(self):
        first = [roll_wobble(random.Random(9)) for _ in range(50)]
        second = [roll_wobble(random.Random(9)) for _ in range(50)]
        self.assertEqual(first, second)

    def test_normalize_shares_scales_to_a_hundred(self):
        self.assertEqual(normalize_shares({"a": 30, "b": 70}), {"a": 30.0, "b": 70.0})
        self.assertEqual(normalize_shares({"a": 60, "b": 40}), {"a": 60.0, "b": 40.0})
        scaled = normalize_shares({"a": 3, "b": 1})
        self.assertAlmostEqual(scaled["a"], 75.0)
        self.assertAlmostEqual(scaled["b"], 25.0)

    def test_normalize_shares_clamps_negatives_first(self):
        # Штраф увёл партию в минус — она обнуляется, а не отбирает долю у
        # остальных: их сумма после этого должна остаться ровно 100 %.
        scaled = normalize_shares({"a": -5, "b": 60, "c": 40})
        self.assertEqual(scaled["a"], 0.0)
        self.assertAlmostEqual(scaled["b"] + scaled["c"], 100.0)

    def test_normalize_shares_of_all_negatives_is_all_zero(self):
        self.assertEqual(normalize_shares({"a": -5, "b": -1}), {"a": 0.0, "b": 0.0})

    def test_weights_excludes_parties_below_the_threshold(self):
        results = {"a": PartyResult(share=60.0), "b": PartyResult(share=THRESHOLD_PERCENT - 0.1),
                  "c": PartyResult(share=THRESHOLD_PERCENT)}
        self.assertEqual(set(weights(results)), {"a", "c"})

    def test_shares_lists_everyone_including_below_threshold(self):
        # Барьер запрещает места, а не показ доли — её видно у всех.
        results = {"a": PartyResult(share=60.0), "b": PartyResult(share=2.0),
                  "c": PartyResult(share=0.0)}
        self.assertEqual(shares(results), {"a": 60.0, "b": 2.0})

    def test_shares_always_add_up_to_a_hundred(self):
        rng = random.Random(7)
        for _ in range(200):
            points = {f"p{i}": rng.randint(0, 12) for i in range(rng.randint(2, 6))}
            base = base_shares(points, sum(points.values()) + rng.randint(0, 8))
            raw = {pid: b + rng.randint(-3, 3) + roll_wobble(rng) for pid, b in base.items()}
            share = normalize_shares(raw)
            total = sum(share.values())
            if total:
                self.assertAlmostEqual(total, 100.0, places=6)

    def test_fractional_shares_still_fill_the_district(self):
        # Доля почти всегда дробная — места всё равно раздаются все до
        # единого.
        results = {"a": PartyResult(share=100 / 3), "b": PartyResult(share=100 / 3),
                  "c": PartyResult(share=100 / 3)}
        seats = allocate_seats(weights(results), 9)
        self.assertEqual(sum(seats.values()), 9)

    def test_result_survives_a_round_trip(self):
        original = PartyResult(base=40.0, national=-1.0, island=2.0, modifier=3.0,
                               wobble=0.5, share=44.5)
        self.assertEqual(PartyResult.from_dict(original.to_dict()), original)

    def test_broken_stored_result_does_not_crash(self):
        # Файл проекта правится руками — мусор в полях не должен ронять загрузку.
        self.assertEqual(PartyResult.from_dict({"base": "ой", "share": None}),
                         PartyResult())

    def test_raw_is_the_sum_before_normalization(self):
        result = PartyResult(base=50.0, national=-2.0, island=1.0, modifier=3.0, wobble=0.5)
        self.assertAlmostEqual(result.raw, 52.5)

    def test_renormalize_shares_out_the_votes_of_whoever_left(self):
        # Ушедшая партия забрала половину округа; её голоса расходятся между
        # оставшимися пропорционально, а не пропадают.
        left = {"b": PartyResult(base=30.0, share=30.0),
                "c": PartyResult(base=20.0, share=20.0)}
        fresh = renormalize(left)
        self.assertAlmostEqual(fresh["b"].share, 60.0)
        self.assertAlmostEqual(fresh["c"].share, 40.0)
        self.assertAlmostEqual(sum(r.share for r in fresh.values()), 100.0)

    def test_renormalize_keeps_the_roll_itself_untouched(self):
        # Пересчитывается только доля: переписывать разыгранные слагаемые
        # задним числом нельзя — разбор перестал бы быть записью о том, что
        # выпало на самом деле.
        before = PartyResult(base=30.0, national=2.0, island=-1.0,
                             modifier=4.0, wobble=0.5, share=12.0)
        after = renormalize({"b": before})["b"]
        self.assertEqual(
            (after.base, after.national, after.island, after.modifier, after.wobble),
            (before.base, before.national, before.island, before.modifier, before.wobble),
        )
        self.assertAlmostEqual(after.share, 100.0)

    def test_renormalize_of_an_all_negative_district_stays_at_zero(self):
        # Поправки увели в минус всех, кто остался: делить нечего, и
        # выдумывать сотню из ничего не нужно.
        left = {"b": PartyResult(base=0.0, modifier=-10.0),
                "c": PartyResult(base=0.0, modifier=-4.0)}
        self.assertEqual({pid: r.share for pid, r in renormalize(left).items()},
                         {"b": 0.0, "c": 0.0})


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
        вообще не участвовали в розыгрыше. Теперь долю получают все, и одной
        поддержки мало — колебание в ±3 п.п. способно перевернуть близкий
        округ. Поэтому рядом с реальной поддержкой регистрируется огромная
        поправка в `self.dominance`: после нормировки она даёт партии почти
        всю сотню, и никакое колебание этого не отыграет.
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
        self.assertEqual(self.conv.results[district.id][self.a.id].modifier, 1000)

        self.service.roll_election(self.conv.id, {}, rng=random.Random(6))
        self.assertEqual(self.conv.results[district.id][self.a.id].modifier, 0)

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

    def test_base_share_counts_from_the_whole_pool_not_from_what_was_given(self):
        # В округе четыре пункта, запас 24 очка. Роздано 8: шесть у a, два у
        # b. Остальные 16 никто не разобрал — это неопределившиеся, они
        # делятся поровну на три партии.
        first, second = self.village.settlements[:2]
        self.service.set_support(self.village.id, first.id, self.a.id, 6)
        self.service.set_support(self.village.id, second.id, self.b.id, 2)
        self.assertEqual(self.service.district_capacity(self.village.id), 24)
        share = lambda p: self.service.base_share(self.village.id, p.id)
        self.assertAlmostEqual(share(self.a), (6 + 16 / 3) / 24 * 100)
        self.assertAlmostEqual(share(self.b), (2 + 16 / 3) / 24 * 100)
        self.assertAlmostEqual(share(self.c), (16 / 3) / 24 * 100)
        self.assertAlmostEqual(share(self.a) + share(self.b) + share(self.c), 100.0)

    def test_a_single_point_gives_an_edge_not_a_landslide(self):
        # Ровно та жалоба, ради которой это и переделано: деревня, где
        # единственная партия завела одного сторонника, доставалась ей
        # целиком — при том что пятерых из шести там не убедил никто.
        lone = self.by_name["Северный мыс"]           # один пункт, запас 6
        self.assertEqual(self.service.district_capacity(lone.id), SETTLEMENT_SUPPORT)
        self.service.set_support(lone.id, lone.settlements[0].id, self.a.id, 1)

        winner = self.service.base_share(lone.id, self.a.id)
        rival = self.service.base_share(lone.id, self.b.id)
        self.assertGreater(winner, rival)             # перевес есть
        self.assertLess(winner, 50.0)                 # но не разгром
        self.assertAlmostEqual(winner, (1 + 5 / 3) / 6 * 100)
        self.assertAlmostEqual(rival, (5 / 3) / 6 * 100)

    def test_taking_the_whole_pool_still_takes_everything(self):
        # Другой край: партия, разобравшая пункт целиком, забирает его весь —
        # неопределившихся там не осталось.
        lone = self.by_name["Северный мыс"]
        self.service.set_support(lone.id, lone.settlements[0].id, self.a.id,
                                 SETTLEMENT_SUPPORT)
        self.assertEqual(self.service.base_share(lone.id, self.a.id), 100.0)
        self.assertEqual(self.service.base_share(lone.id, self.b.id), 0.0)

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

        self.service.set_city_support(city.id, self.a.id, CITY_SUPPORT)
        self.assertEqual(
            self.service.base_share(port.id, self.a.id),
            self.service.base_share(centre.id, self.a.id))
        # Копилка разобрана целиком — город уходит партии полностью, и оба
        # его округа видят это одинаково.
        self.assertEqual(self.service.base_share(port.id, self.a.id), 100.0)
        self.assertEqual(self.service.district_capacity(port.id), CITY_SUPPORT)

    def test_zero_points_remove_the_party(self):
        settlement = self.village.settlements[0]
        self.service.set_support(self.village.id, settlement.id, self.a.id, 3)
        self.service.set_support(self.village.id, settlement.id, self.a.id, 0)
        self.assertEqual(settlement.support, {})

    def test_deleting_a_settlement_with_points_lowers_a_rivals_share(self):
        # Убираем пункт вместе с очками соперника: и его очки, и его запас
        # уходят из округа разом, поэтому доля оставшейся партии растёт.
        first, second = self.village.settlements[:2]
        self.service.set_support(self.village.id, first.id, self.a.id, 4)
        self.service.set_support(self.village.id, second.id, self.b.id, 4)
        before = self.service.base_share(self.village.id, self.a.id)
        self.assertEqual(before, self.service.base_share(self.village.id, self.b.id))

        self.service.delete_settlement(self.village.id, second.id)
        after = self.service.base_share(self.village.id, self.a.id)
        self.assertGreater(after, before)
        self.assertGreater(before, self.service.base_share(self.village.id, self.b.id))
        # Запас округа тоже стал меньше — пункт унёс с собой свои шесть очков.
        self.assertEqual(self.service.district_capacity(self.village.id),
                         3 * SETTLEMENT_SUPPORT)

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

    def test_the_file_keeps_one_copy_of_the_result(self):
        # Веса для дележа мест выводятся из разбора и рядом с ним не
        # хранятся: вторая копия тех же чисел рано или поздно отстаёт от
        # первой — ровно так доли переставали давать сотню после удаления
        # партии.
        import json

        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(5))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        stored = raw["convocations"][0]
        self.assertIn("results", stored)
        self.assertNotIn("votes", stored)

    def test_an_old_file_opens_as_a_manual_composition(self):
        # До перехода на проценты итоги лежали в "rolls" и "votes" — там
        # броски кубика, а не доли, и прочесть их в новую форму нельзя.
        # Состав при этом не теряется: места остаются проставленными, просто
        # набранными как будто руками, и выборы можно сыграть заново.
        import json

        from parlament import store

        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(5))
        seats_before = dict(self.conv.seats)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        stored = raw["convocations"][0]
        stored["rolls"] = stored.pop("results")
        stored["votes"] = {self.district.id: {self.a.id: 7}}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        reopened = store.load(self.path)
        conv = reopened.active_convocation
        self.assertEqual(conv.seats, seats_before)
        self.assertEqual(conv.results, {})
        self.assertFalse(conv.has_election)

    def test_every_party_takes_part_regardless_of_support(self):
        # Раньше в округ лезли только партии с поддержкой или модификатором —
        # это и была ошибка: бросают все, а поддержка лишь прибавляется.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id,
                                   {self.district.id: {self.b.id: {"modifier": 1}}},
                                   rng=random.Random(1))
        taking_part = set(self.conv.results[self.district.id])
        self.assertEqual(taking_part, {self.a.id, self.b.id, self.c.id})

    def test_base_reaches_the_result(self):
        # Поровну розданные очки дают равную базу, сколько бы пунктов ни
        # было: важна сумма, а не то, в каком пункте они лежат.
        self.give_support(self.a, 5)
        self.give_support(self.b, 5, where=1)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(2))
        per_party = self.conv.results[self.district.id]
        self.assertEqual(per_party[self.a.id].base, per_party[self.b.id].base)
        # Партия без очков идёт не с нуля: неразобранные очки — это
        # неопределившиеся, они делятся поровну на всех.
        self.assertGreater(per_party[self.c.id].base, 0)
        self.assertGreater(per_party[self.a.id].base, per_party[self.c.id].base)
        self.assertAlmostEqual(sum(r.base for r in per_party.values()), 100.0)

    def test_modifier_reaches_the_result(self):
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: {"modifier": -3}}},
            rng=random.Random(5))
        result = self.conv.results[self.district.id][self.a.id]
        self.assertEqual(result.modifier, -3)

    def test_national_mood_reaches_every_district(self):
        # Один и тот же модификатор партии должен прибавиться в каждом
        # округе — это и есть смысл «настроения по стране».
        self.service.roll_election(
            self.conv.id, {}, national={self.a.id: 4}, rng=random.Random(23))
        for district_id, per_party in self.conv.results.items():
            self.assertEqual(per_party[self.a.id].national, 4, district_id)
            self.assertEqual(per_party[self.b.id].national, 0, district_id)

    def test_national_mood_stacks_with_a_local_modifier(self):
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: {"modifier": 2}}},
            national={self.a.id: 3}, rng=random.Random(24))
        result = self.conv.results[self.district.id][self.a.id]
        self.assertEqual(result.modifier, 2)
        self.assertEqual(result.national, 3)

    def test_bad_national_mood_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, national={self.a.id: "ой"})

    def test_national_mood_for_an_unknown_party_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, national={"нет-такой": 1})

    def test_island_swing_reaches_every_district_of_that_island(self):
        island = island_of(self.district.region)   # Судбригг: Остров Нурик
        self.service.roll_election(
            self.conv.id, {}, island={island: {self.a.id: 5}}, rng=random.Random(25))
        self.assertEqual(self.conv.results[self.district.id][self.a.id].island, 5)

    def test_island_swing_does_not_leak_to_other_islands(self):
        island = island_of(self.district.region)
        other = self.by_name["Херсвикский"]         # другой остров (Каспиан)
        self.assertNotEqual(island_of(other.region), island)
        self.service.roll_election(
            self.conv.id, {}, island={island: {self.a.id: 5}}, rng=random.Random(26))
        self.assertEqual(self.conv.results[other.id][self.a.id].island, 0)

    def test_island_swing_stacks_with_national_mood_and_local_modifier(self):
        island = island_of(self.district.region)
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: {"modifier": 1}}},
            national={self.a.id: 2}, island={island: {self.a.id: 3}},
            rng=random.Random(27))
        result = self.conv.results[self.district.id][self.a.id]
        self.assertEqual((result.modifier, result.national, result.island), (1, 2, 3))

    def test_bad_island_swing_is_rejected(self):
        island = island_of(self.district.region)
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, island={island: {self.a.id: "ой"}})

    def test_unknown_island_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, island={"Атлантида": {self.a.id: 1}})

    def test_island_swing_for_an_unknown_party_is_rejected(self):
        island = island_of(self.district.region)
        with self.assertRaises(ValidationError):
            self.service.roll_election(self.conv.id, {}, island={island: {"нет-такой": 1}})

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

    def test_results_are_stored_for_the_record(self):
        # Колебание случайно и не повторится: без сохранённой разбивки
        # потом не понять, из чего сложился результат.
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(13))
        again = ParlamentService(self.path)
        again.bootstrap()
        stored = again.project.active_convocation.results[self.district.id][self.a.id]
        self.assertEqual(stored, self.conv.results[self.district.id][self.a.id])

    def test_empty_setup_still_fills_the_whole_parliament(self):
        # Пустая настройка — не «нечего разыгрывать»: без поддержки и
        # поправок все партии всё равно получают долю на одном колебании.
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
        before = dict(self.conv.results)

        self.service.roll_election(self.conv.id, {}, rng=random.Random(22))
        self.assertNotEqual(self.conv.results, before)
        self.assertEqual(sum(self.conv.seats.values()), SEED_TOTAL_SEATS)

    def test_clearing_wipes_the_rolls_too(self):
        self.give_support(self.a, 4)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(19))
        self.service.clear_election(self.conv.id)
        self.assertEqual(self.conv.results, {})
        self.assertEqual(self.service.district_allocation(self.conv.id), {})

    def test_bad_modifier_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.roll_election(
                self.conv.id, {self.district.id: {self.a.id: {"modifier": "ой"}}})


class TestVoteShares(ElectionTestCase):
    """Доля голосов по стране — рядом с местами её и показывают."""

    def roll(self, seed: int = 5):
        self.service.roll_election(self.conv.id, {}, rng=random.Random(seed))
        return self.service.vote_shares(self.conv.id)

    def test_without_an_election_there_are_no_votes(self):
        # Не нули, а именно пусто: мест могли набрать руками, и голосов за
        # ними не стоит никаких.
        self.assertEqual(self.service.vote_shares(self.conv.id), {})

    def test_shares_add_up_to_a_hundred(self):
        votes = self.roll()
        self.assertAlmostEqual(sum(votes.values()), 100.0, places=6)
        self.assertEqual(set(votes), {self.a.id, self.b.id, self.c.id})

    def test_a_bigger_district_pulls_harder(self):
        # Округа взвешены по мандатам: округ на десять мест представляет
        # больше людей, чем округ на два, и считать их наравне значило бы
        # приравнять хутор к столице.
        big = max(self.service.project.districts, key=lambda d: d.seats)
        small = min((d for d in self.service.project.districts
                     if d.settlements and d.id != big.id),
                    key=lambda d: d.seats)
        self.assertGreater(big.seats, small.seats)

        # Одна и та же поправка, но в разных по весу округах.
        self.service.roll_election(self.conv.id, {
            big.id: {self.a.id: {"modifier": 1000}},
            small.id: {self.b.id: {"modifier": 1000}},
        }, rng=random.Random(3))
        votes = self.service.vote_shares(self.conv.id)
        self.assertGreater(votes[self.a.id], votes[self.b.id])

    def test_votes_and_seats_are_allowed_to_disagree(self):
        # Ради этого их и показывают рядом: округа делятся по большинству,
        # и доля мест партии не обязана совпадать с долей голосов.
        # Уверенно берём половину округов: там уходят все мандаты, а в
        # остальных партия идёт наравне со всеми — мест выходит больше, чем
        # голосов. Это и есть перекос, который эти два числа показывают.
        half = self.service.project.districts[::2]
        self.service.roll_election(self.conv.id, {
            d.id: {self.a.id: {"modifier": 1000}} for d in half
        }, rng=random.Random(8))
        votes = self.service.vote_shares(self.conv.id)
        seats = self.conv.seats
        share_of_seats = seats[self.a.id] / sum(seats.values()) * 100
        self.assertGreater(
            share_of_seats, votes[self.a.id] + 1,
            "перевес в округах должен давать мест заметно больше, чем голосов")

    def test_a_party_below_the_threshold_still_has_votes(self):
        # Барьер запрещает места, а не голоса: доля у партии есть, а мандатов
        # может не быть вовсе.
        self.service.roll_election(self.conv.id, {
            d.id: {self.a.id: {"modifier": 1000}}
            for d in self.service.project.districts
        }, rng=random.Random(12))
        votes = self.service.vote_shares(self.conv.id)
        self.assertGreater(votes[self.b.id], 0)
        self.assertEqual(self.conv.seats.get(self.b.id, 0), 0)

    def test_clearing_the_election_takes_the_votes_with_it(self):
        self.roll()
        self.service.clear_election(self.conv.id)
        self.assertEqual(self.service.vote_shares(self.conv.id), {})


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

    Без всякой поддержки база делится поровну (33,3 % на три партии), а
    колебание — не больше ±`WOBBLE_RANGE`, так что штраф -40 гарантированно
    топит каждую партию в ноль независимо от того, как выпадет колебание.
    """

    def setUp(self):
        super().setUp()
        self.district = self.by_name["Судбригг"]
        penalty = {"modifier": -40}
        self.service.roll_election(
            self.conv.id,
            {self.district.id: {self.a.id: penalty, self.b.id: penalty,
                                self.c.id: penalty}},
            rng=random.Random(43))

    def test_the_result_is_kept_for_the_record(self):
        self.assertEqual(self.conv.results[self.district.id][self.a.id].share, 0.0)
        # Разбор остался, но делить в округе нечего: доли нулевые у всех.
        self.assertEqual(self.service.district_shares(self.conv.id, self.district.id), {})
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

    def test_a_party_held_under_the_threshold_gets_seats_once_its_rival_is_gone(self):
        # Барьер посчитан по раскладу с соперником: пока он был, мелкая
        # партия оставалась под пятью процентами и мест не получала. Если
        # доли не пересчитать, она так и осталась бы ни с чем — при том что
        # в округе, кроме неё, уже никого.
        # Соперник забирает округ почти целиком, мелкой партии остаётся
        # положительная, но крохотная доля: +5 п.п. против колебания в ±3
        # никогда не уходят в минус, так что расклад не зависит от зерна.
        self.service.roll_election(self.conv.id, {
            self.district.id: {self.a.id: {"modifier": 1000},
                               self.b.id: {"modifier": 5}},
        }, rng=random.Random(11))
        small = self.conv.results[self.district.id][self.b.id].share
        self.assertGreater(small, 0)
        self.assertLess(small, THRESHOLD_PERCENT)
        self.assertEqual(self.service.district_allocation(self.conv.id)
                         .get(self.district.id, {}).get(self.b.id, 0), 0)

        self.service.delete_party(self.a.id)
        self.service.delete_party(self.c.id)
        shares_now = self.service.district_shares(self.conv.id, self.district.id)
        self.assertAlmostEqual(shares_now[self.b.id], 100.0, places=6)
        self.assertEqual(self.service.district_allocation(self.conv.id)[self.district.id],
                         {self.b.id: self.district.seats})

    def test_it_leaves_the_rolled_districts(self):
        # Бросают все партии, включая ту, что без ставок (self.c) — она
        # остаётся в разборе округа и после удаления соседей.
        self.service.set_support(self.district.id, self.settlement.id, self.a.id, 3)
        self.service.set_support(self.district.id, self.settlement.id, self.b.id, 3)
        self.service.roll_election(self.conv.id, {}, rng=random.Random(31))
        self.assertEqual(set(self.conv.results[self.district.id]),
                         {self.a.id, self.b.id, self.c.id})

        self.service.delete_party(self.a.id)
        self.assertEqual(set(self.conv.results[self.district.id]), {self.b.id, self.c.id})
        # Голоса ушедшей партии расходятся между оставшимися: округ снова
        # даёт сотню, а не «16,9 % и все пять мест».
        shares = self.service.district_shares(self.conv.id, self.district.id)
        self.assertNotIn(self.a.id, shares)
        self.assertAlmostEqual(sum(shares.values()), 100.0, places=6)

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

    def test_deleting_a_party_removes_only_its_own_result(self):
        # У партии с одним лишь штрафом всё равно есть своя запись в разборе
        # округа. Удаление партии стирает именно её запись — другие партии в
        # этом же округе остаются как были.
        self.service.roll_election(
            self.conv.id, {self.district.id: {self.a.id: {"modifier": -20}}},
            rng=random.Random(37))
        self.assertIn(self.a.id, self.conv.results[self.district.id])

        self.service.delete_party(self.a.id)
        self.assertNotIn(self.a.id, self.conv.results[self.district.id])
        self.assertIn(self.b.id, self.conv.results[self.district.id])

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
        self.assertEqual(self.conv.results, {})
        self.assertEqual(self.service.district_allocation(self.conv.id), {})
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

    def test_base_share_follows_the_imported_table(self):
        district = self.by_name["Судбригг"]
        result = self.parse(self.table("Судбригг,Судурей,4,,\n"
                                       "Судбригг,Фьярей,2,,\n"))
        self.service.import_support(result.rows)
        # Шесть очков из двенадцати достались единственной партии: перевес
        # заметный, но остальные шесть никто не разобрал, и они делятся
        # поровну — разгрома не выходит.
        self.assertEqual(self.service.district_capacity(district.id), 12)
        share = self.service.base_share(district.id, self.a.id)
        self.assertAlmostEqual(share, (6 + 6 / 3) / 12 * 100)
        self.assertGreater(share, self.service.base_share(district.id, self.b.id))
        self.assertLess(share, 100.0)

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
        # База одинакова на любом округе этого города.
        self.assertEqual(
            self.service.base_share(gaffinsvik.id, self.a.id),
            self.service.base_share(self.by_name["Гаффинсвик порт"].id, self.a.id))

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
