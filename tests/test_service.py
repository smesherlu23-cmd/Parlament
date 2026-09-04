"""Тесты бизнес-логики: валидация мест, справочник партий, созывы, файл."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlament import ParlamentService, ValidationError  # noqa: E402
from parlament.district_geometry import DISTRICT_SHAPES  # noqa: E402
from parlament.district_seed import SEED_TOTAL_SEATS  # noqa: E402
from parlament.model import Project, convocation_name, normalize_color  # noqa: E402
from parlament.store import StoreError, load, save, set_aside  # noqa: E402


class ServiceTestCase(unittest.TestCase):
    """Общая заготовка: сервис на временном файле и пара партий."""

    def setUp(self):
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "проект.parlament.json"
        self.service = ParlamentService(self.path)
        self.service.bootstrap()

    def party(self, name="Народный союз", color="#0088b0", abbr="НС"):
        return self.service.create_party(name=name, color=color, abbr=abbr)

    @property
    def active_id(self):
        return self.service.project.active_convocation.id


class TestColorNormalisation(unittest.TestCase):
    def test_accepts_common_forms(self):
        for value in ("#3A7CA5", "3A7CA5", "#3a7ca5"):
            self.assertEqual(normalize_color(value), "#3a7ca5")

    def test_expands_shorthand(self):
        self.assertEqual(normalize_color("#abc"), "#aabbcc")

    def test_rejects_garbage(self):
        for value in ("", "не цвет", "#12345", "#gggggg"):
            with self.assertRaises(ValueError):
                normalize_color(value)


class TestConvocationNaming(unittest.TestCase):
    def test_ordinal_names(self):
        self.assertEqual(convocation_name(1), "Первый состав")
        self.assertEqual(convocation_name(3), "Третий состав")
        self.assertEqual(convocation_name(20), "Двадцатый состав")

    def test_falls_back_to_number(self):
        self.assertEqual(convocation_name(21), "21-й состав")


class TestParties(ServiceTestCase):
    def test_create_normalises_input(self):
        party = self.service.create_party(name="  Партия труда ", color="D6006C", abbr=" ПТ ")
        self.assertEqual(party.name, "Партия труда")
        self.assertEqual(party.color, "#d6006c")
        self.assertEqual(party.abbr, "ПТ")

    def test_empty_name_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_party(name="   ", color="#0088b0")

    def test_bad_color_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_party(name="Партия", color="синий")

    def test_update_propagates_everywhere(self):
        # Сценарий 6 из ТЗ: партия переименовалась по ходу игры.
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 40)
        self.service.update_party(party.id, name="Новый союз", color="#4c7a34", abbr="НС")

        stored = self.service.project.party(party.id)
        self.assertEqual(stored.name, "Новый союз")
        self.assertEqual(stored.color, "#4c7a34")
        # Места остались за той же партией — ничего не потерялось.
        self.assertEqual(self.service.project.active_convocation.seats[party.id], 40)

    def test_delete_frees_seats_in_every_convocation(self):
        keep, drop = self.party(), self.party(name="Аграрный блок", color="#4c7a34")
        self.service.set_seats(self.active_id, keep.id, 30)
        self.service.set_seats(self.active_id, drop.id, 20)
        self.service.fix_convocation()
        self.service.set_seats(self.active_id, drop.id, 15)

        self.service.delete_party(drop.id)

        self.assertIsNone(self.service.project.party(drop.id))
        for conv in self.service.project.convocations:
            self.assertNotIn(drop.id, conv.seats)
        # Соседняя партия не пострадала.
        self.assertEqual(self.service.project.convocations[0].seats[keep.id], 30)

    def test_usage_lists_affected_convocations(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 16)
        self.service.fix_convocation()
        self.service.set_seats(self.active_id, party.id, 18)

        usage = self.service.party_usage(party.id)
        self.assertEqual([u["seats"] for u in usage], [16, 18])
        self.assertEqual(usage[0]["convocationName"], "Первый состав")

    def test_delete_unknown_party_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.delete_party("нет-такой")


class TestSeatValidation(ServiceTestCase):
    def test_seats_are_stored(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 61)
        self.assertEqual(self.service.project.active_convocation.seats[party.id], 61)

    def test_zero_removes_the_entry(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 10)
        self.service.set_seats(self.active_id, party.id, 0)
        self.assertEqual(self.service.project.active_convocation.seats, {})

    def test_negative_rejected(self):
        party = self.party()
        with self.assertRaises(ValidationError):
            self.service.set_seats(self.active_id, party.id, -1)

    def test_non_integer_rejected(self):
        party = self.party()
        for value in ("много", None, 3.5, True):
            with self.assertRaises(ValidationError):
                self.service.set_seats(self.active_id, party.id, value)

    def test_sum_cannot_exceed_total(self):
        # Числа считаются от размера парламента, а не зашиты: он задаётся
        # картой округов и уже однажды менялся со 120 на 147.
        total = self.service.project.total_seats
        first, second = self.party(), self.party(name="Партия труда", color="#d6006c")
        self.service.set_seats(self.active_id, first.id, total - 20)
        with self.assertRaises(ValidationError) as ctx:
            self.service.set_seats(self.active_id, second.id, 21)
        self.assertIn("20", str(ctx.exception))  # подсказка про доступный остаток

    def test_exact_total_allowed(self):
        total = self.service.project.total_seats
        first, second = self.party(), self.party(name="Партия труда", color="#d6006c")
        self.service.set_seats(self.active_id, first.id, total - 20)
        self.service.set_seats(self.active_id, second.id, 20)
        self.assertEqual(self.service.project.active_convocation.used_seats(), total)

    def test_set_all_rejects_overflow_atomically(self):
        total = self.service.project.total_seats
        first, second = self.party(), self.party(name="Партия труда", color="#d6006c")
        self.service.set_seats(self.active_id, first.id, 50)
        with self.assertRaises(ValidationError):
            self.service.set_all_seats(self.active_id, {first.id: total, second.id: 1})
        # Ничего не применилось — распределение осталось прежним.
        self.assertEqual(self.service.project.active_convocation.seats, {first.id: 50})

    def test_reset_clears_distribution(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 42)
        self.service.reset_seats(self.active_id)
        self.assertEqual(self.service.project.active_convocation.seats, {})

    def test_seats_for_unknown_party_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.set_seats(self.active_id, "нет-такой", 5)


class TestConvocations(ServiceTestCase):
    def test_first_convocation_exists_on_start(self):
        self.assertEqual(len(self.service.project.convocations), 1)
        self.assertEqual(self.service.project.convocations[0].name, "Первый состав")

    def test_fix_archives_current_and_opens_next(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 70)
        first_id = self.active_id

        fresh = self.service.fix_convocation()

        archived = self.service.project.convocation(first_id)
        self.assertTrue(archived.is_fixed)
        self.assertEqual(archived.seats[party.id], 70)   # снимок сохранился
        self.assertEqual(fresh.name, "Второй состав")
        self.assertEqual(fresh.seats, {})                # новый созыв пустой
        self.assertEqual(self.service.project.active_convocation.id, fresh.id)

    def test_fix_accepts_custom_name(self):
        fresh = self.service.fix_convocation("Второй состав (кризисные выборы)")
        self.assertEqual(fresh.name, "Второй состав (кризисные выборы)")

    def test_archived_convocations_stay_editable(self):
        # Продуктовое решение: историю можно править, новый созыв при этом
        # не создаётся.
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 10)
        archived_id = self.active_id
        self.service.fix_convocation()

        self.service.set_seats(archived_id, party.id, 12)

        self.assertEqual(self.service.project.convocation(archived_id).seats[party.id], 12)
        self.assertEqual(len(self.service.project.convocations), 2)

    def test_rename(self):
        self.service.rename_convocation(self.active_id, "  Третий состав (кризис) ")
        self.assertEqual(self.service.project.active_convocation.name, "Третий состав (кризис)")

    def test_rename_to_blank_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.rename_convocation(self.active_id, "  ")

    def test_next_name_hint(self):
        self.assertEqual(self.service.next_convocation_name(), "Второй состав")
        self.service.fix_convocation()
        self.assertEqual(self.service.next_convocation_name(), "Третий состав")


class TestDeleteConvocation(ServiceTestCase):
    """Удаление созыва из истории — раздел 9 ТЗ отмечал это как открытый
    вопрос; решение: удалять можно, кроме единственного оставшегося."""

    def test_removes_and_renumbers(self):
        first_id = self.active_id
        self.service.fix_convocation()               # 2-й активен
        self.service.fix_convocation()                # 3-й активен

        self.service.delete_convocation(first_id)

        numbers = [(c.number, c.name) for c in self.service.project.convocations]
        self.assertEqual(numbers, [(1, "Первый состав"), (2, "Второй состав")])

    def test_auto_names_resync_to_new_position(self):
        # «Третий состав» держит автосгенерированное имя — после удаления
        # второго созыва он должен стать «Вторым составом» и по номеру,
        # и по названию.
        second_id = self.active_id
        self.service.fix_convocation()
        self.service.fix_convocation()

        self.service.delete_convocation(second_id)

        remaining = self.service.project.convocations[1]
        self.assertEqual(remaining.number, 2)
        self.assertEqual(remaining.name, "Второй состав")

    def test_custom_name_survives_renumbering(self):
        # А вручную переименованный созыв — нет: пользовательский текст
        # не подменяем, даже если он больше не совпадает с позицией.
        first_id = self.active_id
        second = self.service.fix_convocation()
        self.service.rename_convocation(second.id, "Второй состав (кризис)")
        self.service.fix_convocation()

        self.service.delete_convocation(first_id)

        remaining = self.service.project.convocations[0]
        self.assertEqual(remaining.number, 1)
        self.assertEqual(remaining.name, "Второй состав (кризис)")

    def test_deleting_active_reopens_previous(self):
        first_id = self.active_id
        self.service.fix_convocation()
        active_id = self.service.project.active_convocation.id

        fresh_active = self.service.delete_convocation(active_id)

        self.assertEqual(fresh_active.id, first_id)
        self.assertFalse(fresh_active.is_fixed)
        self.assertEqual(len(self.service.project.convocations), 1)

    def test_deleting_archived_keeps_active_untouched(self):
        first_id = self.active_id
        self.service.fix_convocation()
        active_id = self.service.project.active_convocation.id

        fresh_active = self.service.delete_convocation(first_id)

        self.assertEqual(fresh_active.id, active_id)
        self.assertFalse(fresh_active.is_fixed)

    def test_cannot_delete_the_only_convocation(self):
        with self.assertRaises(ValidationError):
            self.service.delete_convocation(self.active_id)
        self.assertEqual(len(self.service.project.convocations), 1)

    def test_unknown_convocation_rejected(self):
        self.service.fix_convocation()
        with self.assertRaises(ValidationError):
            self.service.delete_convocation("нет-такой")

    def test_deletion_is_persisted(self):
        first_id = self.active_id
        self.service.fix_convocation()

        self.service.delete_convocation(first_id)

        reloaded = load(self.path)
        self.assertEqual(len(reloaded.convocations), 1)


class TestBrokenFileIsRepaired(unittest.TestCase):
    """Файл проекта правится руками — из него не должно вылезать битое
    состояние: открытым обязан быть последний созыв, и хотя бы один."""

    def project(self, convocations: list[dict]) -> Project:
        return Project.from_dict({"schemaVersion": 2, "totalSeats": 147,
                                  "parties": [], "convocations": convocations})

    def test_open_convocation_in_the_middle_is_closed(self):
        # Иначе следующий созыв получил бы уже занятый номер.
        project = self.project([
            {"id": "c1", "number": 1, "name": "Первый состав", "fixedAt": "2020-01-01"},
            {"id": "c2", "number": 2, "name": "Второй состав"},
            {"id": "c3", "number": 3, "name": "Третий состав", "fixedAt": "2020-03-01"},
        ])
        self.assertEqual(project.active_convocation.id, "c3")
        self.assertEqual([c.number for c in project.convocations], [1, 2, 3])
        self.assertTrue(project.convocation("c2").is_fixed)

    def test_all_convocations_fixed_reopens_the_last(self):
        project = self.project([
            {"id": "c1", "number": 1, "name": "Первый", "fixedAt": "2020-01-01"},
            {"id": "c2", "number": 2, "name": "Второй", "fixedAt": "2020-02-01"},
        ])
        self.assertEqual(project.active_convocation.id, "c2")

    def test_file_without_convocations_gets_one(self):
        project = self.project([])
        self.assertEqual(len(project.convocations), 1)
        self.assertFalse(project.active_convocation.is_fixed)


class TestPersistence(ServiceTestCase):
    def test_every_change_is_written_to_disk(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 33)

        reloaded = load(self.path)
        self.assertEqual(reloaded.parties[0].name, "Народный союз")
        self.assertEqual(reloaded.active_convocation.seats[party.id], 33)

    def test_file_is_readable_utf8_json(self):
        self.party()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["totalSeats"], SEED_TOTAL_SEATS)
        self.assertEqual(raw["parties"][0]["name"], "Народный союз")

    def test_bootstrap_reopens_previous_project(self):
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 55)

        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(again.project.active_convocation.used_seats(), 55)

    def test_new_project_starts_clean_and_persists(self):
        self.party()
        self.service.set_seats(self.active_id, self.service.project.parties[0].id, 40)

        self.service.new_project()

        self.assertEqual(self.service.project.parties, [])
        self.assertEqual(len(self.service.project.convocations), 1)
        self.assertEqual(self.service.path, self.service.default_path)
        # Чистый проект тоже сразу лёг на диск, а не только в памяти.
        self.assertEqual(load(self.path).parties, [])

    def test_save_as_switches_target_file(self):
        other = self.path.parent / "другая-игра.parlament.json"
        self.party()
        self.service.save_project_as(other)
        self.assertTrue(other.exists())

        self.service.create_party(name="Вторая", color="#d6006c")
        self.assertEqual(len(load(other).parties), 2)   # пишем уже в новый файл

    def test_open_replaces_current_project(self):
        other = self.path.parent / "вторая-игра.parlament.json"
        save(Project.empty(), other)
        self.party()

        self.service.open_project(other)
        self.assertEqual(self.service.project.parties, [])

    def test_broken_file_reports_clearly(self):
        self.path.write_text("{ это не json", encoding="utf-8")
        with self.assertRaises(StoreError):
            load(self.path)

    def test_orphan_seats_are_dropped_on_load(self):
        # Файл поправили вручную и удалили партию — места должны стать
        # нераспределёнными, а не сломать загрузку.
        party = self.party()
        self.service.set_seats(self.active_id, party.id, 20)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["parties"] = []
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        project = load(self.path)
        self.assertEqual(project.convocations[0].seats, {})

    def test_atomic_write_leaves_no_temp_files(self):
        self.party()
        leftovers = [p.name for p in self.path.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


class TestMapCorrectionsReachOldProjects(ServiceTestCase):
    """Уточнение карты доезжает до уже начатых партий."""

    def old_file(self) -> None:
        """Портит в сохранённом файле то, что задаёт карта, а не пользователь."""
        import json

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw["districts"]:
            item["name"] = f"Старое имя {item['code']}"
            item["region"] = "Старый регион"
            item["seats"] = 3
            item["x"], item["y"] = 0.42, 0.42
        raw["totalSeats"] = 120
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def test_names_seats_and_size_come_from_the_map(self):
        # Переименовать округ в программе нельзя — значит, это не правка
        # пользователя, а устаревшая карта.
        self.party()
        self.old_file()
        project = load(self.path)

        district = next(d for d in project.districts if d.code == 11)
        self.assertEqual(district.name, "Гаффинсвик центр")
        self.assertEqual(district.region, "Гаффинсвик")
        self.assertEqual(project.total_seats, SEED_TOTAL_SEATS)

    def test_settlements_and_points_survive(self):
        party = self.party()
        service = self.service
        district = service.project.districts[10]
        settlement = service.add_settlement(district.id, "Гавань")
        service.set_support(district.id, settlement.id, party.id, 5)
        self.old_file()

        project = load(self.path)
        kept = next(d for d in project.districts if d.id == district.id)
        # Пункты с карты дописались, а заведённый руками остался с очками.
        self.assertIn("Гавань", [s.name for s in kept.settlements])
        self.assertEqual(kept.support_points(party.id), 5)

    def test_project_without_districts_is_left_alone(self):
        # Проект, начатый до карты: досочинять ему округа нельзя, иначе
        # разъедется набранный руками состав.
        project = Project.from_dict({"schemaVersion": 1, "totalSeats": 120,
                                     "parties": [], "convocations": []})
        self.assertEqual(project.districts, [])
        self.assertEqual(project.total_seats, 120)


class TestOldProjectWithoutDistrictCodes(ServiceTestCase):
    """Файл сборки, где округа уже были, а номеров на карте ещё не было."""

    #: Как выглядел округ в тех файлах: имя, места, регион и координаты
    #: маркера — без `code`, потому что его тогда не существовало.
    OLD = [("Западный берег", 6, "Саттмалвик"), ("Холмавикский", 5, "Саттмалвик"),
           ("Скалафьялльский", 4, "Саттмалвик"), ("Саттмалвик центр", 9, "Саттмалвик"),
           ("Саттмалвик порт", 7, "Саттмалвик"), ("Северный мыс", 4, "Саттмалвик"),
           ("Херсвикский", 5, "Гаффинсвик"), ("Гаффинсвик север", 6, "Гаффинсвик"),
           ("Гаффинсвик промышленный", 6, "Гаффинсвик"), ("Раудихолмский", 5, "Гаффинсвик"),
           ("Офридархолтский", 4, "Гаффинсвик"), ("Гаффинсвик центр", 10, "Гаффинсвик"),
           ("Гаффинсвик порт", 8, "Гаффинсвик"), ("Гаффинсвик юг", 5, "Гаффинсвик"),
           ("Трэлавикский", 5, "Гаффинсвик"), ("Скьяльдарстадирский", 4, "Гаффинсвик"),
           ("Стьорнавикский", 4, "Нивенсхолл"), ("Намавикский", 5, "Нивенсхолл"),
           ("Нивенсхолл шахтный", 6, "Нивенсхолл"), ("Нивенсхолл центр", 8, "Нивенсхолл"),
           ("Нивенсхолл порт", 6, "Нивенсхолл"), ("Нивенсхолл восток", 5, "Нивенсхолл"),
           ("Нивенсфелльский", 3, "Нивенсхолл"), ("Киркьюнивенский", 4, "Триединсборг"),
           ("Триединсборг шахтный", 5, "Триединсборг"),
           ("Триединсборг центр", 6, "Триединсборг"), ("Судбригг", 2, "Судбригг")]

    def write_old_file(self) -> None:
        import json

        from parlament.model import new_id

        raw = {
            "schemaVersion": 2, "totalSeats": 147, "rows": 5,
            "parties": [{"id": "p1", "name": "Народный союз", "color": "#0088b0"}],
            "convocations": [{"id": "c1", "number": 1, "name": "Первый состав"}],
            "districts": [{"id": new_id("d"), "name": name, "seats": seats,
                           "region": region, "x": 0.5, "y": 0.5}
                          for name, seats, region in self.OLD],
        }
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def test_codes_are_restored_by_name(self):
        # Без номера округ не с чем связать — ни границ, ни подписи, и карта
        # у такого проекта оставалась пустой.
        self.write_old_file()
        project = load(self.path)

        codes = sorted(d.code for d in project.districts)
        self.assertEqual(codes, list(range(1, 28)))
        by_code = {d.code: d.name for d in project.districts}
        self.assertEqual(by_code[11], "Гаффинсвик центр")
        self.assertEqual(by_code[20], "Судбригг")

    def test_every_district_can_be_drawn(self):
        self.write_old_file()
        project = load(self.path)
        for district in project.districts:
            self.assertIn(district.code, DISTRICT_SHAPES, district.name)

    def test_settlements_and_ids_survive(self):
        self.write_old_file()
        project = load(self.path)
        before = [d.id for d in project.districts]

        service = ParlamentService(self.path)
        service.bootstrap()
        district = service.project.districts[0]
        service.add_settlement(district.id, "Гавань")

        again = load(self.path)
        self.assertEqual([d.id for d in again.districts], before)
        self.assertIn("Гавань", [s.name for s in again.districts[0].settlements])


class TestBrokenFileIsSetAside(ServiceTestCase):
    """Нечитаемый файл проекта не затирается чистым."""

    def test_it_is_renamed_and_kept(self):
        # Файл — единственная копия игры: программа его не понимает, но
        # руками из него нередко можно вытащить всё.
        self.path.write_text("{ это не json", encoding="utf-8")
        service = ParlamentService(self.path)
        with self.assertRaises(StoreError):
            service.bootstrap()

        saved = set_aside(self.path)
        self.assertIsNotNone(saved)
        self.assertIn("broken", saved.name)
        self.assertEqual(saved.read_text(encoding="utf-8"), "{ это не json")
        self.assertFalse(self.path.exists())

    def test_a_clean_project_then_writes_beside_it(self):
        self.path.write_text("{ это не json", encoding="utf-8")
        saved = set_aside(self.path)
        service = ParlamentService(self.path)
        service.bootstrap()
        service.create_party(name="Народный союз", color="#0088b0")

        self.assertTrue(self.path.exists())
        self.assertEqual(saved.read_text(encoding="utf-8"), "{ это не json")

    def test_missing_file_is_not_an_error(self):
        self.assertIsNone(set_aside(self.path.with_name("нет-такого.json")))


class TestRecentColors(ServiceTestCase):
    def test_remembered_color_is_written_to_disk(self):
        self.service.remember_recent_color("#a1b2c3")

        reloaded = load(self.path)
        self.assertEqual(reloaded.recent_colors, ["#a1b2c3"])

    def test_survives_service_restart(self):
        self.service.remember_recent_color("#a1b2c3")

        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(again.project.recent_colors, ["#a1b2c3"])

    def test_capped_deduplicated_and_most_recent_first(self):
        colors = [f"#{n:02x}{n:02x}{n:02x}" for n in range(1, 12)]   # 11 цветов
        for color in colors:
            self.service.remember_recent_color(color)
        # Хранится 10 последних — самый первый (#010101) вытеснен.
        self.assertEqual(self.service.project.recent_colors,
                          list(reversed(colors[1:])))

        self.service.remember_recent_color(colors[5])
        self.assertEqual(self.service.project.recent_colors[0], colors[5])
        self.assertEqual(len(self.service.project.recent_colors), 10)

    def test_bad_color_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.remember_recent_color("не цвет")


class TestGameFlow(ServiceTestCase):
    """Сценарии 1-4 из ТЗ одним прогоном."""

    def test_full_game_flow(self):
        left = self.party()
        right = self.party(name="Партия труда", color="#d6006c", abbr="ПТ")

        self.service.set_seats(self.active_id, left.id, 65)
        self.service.set_seats(self.active_id, right.id, 55)
        first_id = self.active_id
        self.service.fix_convocation()

        project = self.service.project
        self.assertEqual(len(project.convocations), 2)
        self.assertEqual(project.convocation(first_id).seats[left.id], 65)
        self.assertEqual(project.active_convocation.seats, {})
        self.assertNotEqual(project.active_convocation.id, first_id)

    def test_is_default_project_flag(self):
        self.assertTrue(self.service.is_default_project)
        other = self.path.parent / "другая.parlament.json"
        self.service.save_project_as(other)
        self.assertFalse(self.service.is_default_project)


if __name__ == "__main__":
    unittest.main(verbosity=2)
