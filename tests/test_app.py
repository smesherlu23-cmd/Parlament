"""Тесты интерфейса: сценарии ТЗ, прогнанные через настоящий `ParlamentApp`.

Приложение собирается на подставной странице (`FakePage`), поэтому проверки
идут без запуска окна, но через тот же код, что работает у пользователя:
те же обработчики полей, те же диалоги, тот же `service`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flet as ft  # noqa: E402

from fake_page import FakePage, find, find_all, texts  # noqa: E402
from parlament.service import ParlamentService  # noqa: E402
from parlament.ui import theme  # noqa: E402
from parlament.ui.app import ParlamentApp  # noqa: E402
from parlament.ui.dialogs import normalize_hex  # noqa: E402
from parlament.ui.export import LegendEntry, render_png, suggest_file_name  # noqa: E402
from parlament.ui.seat_chart import compute_seats  # noqa: E402

SAMPLE = [
    ("Народный союз", "НС", "#0088b0", 34),
    ("Партия труда", "ПТ", "#d6006c", 27),
    ("Аграрный блок", "АБ", "#4c7a34", 18),
    ("Либеральный форум", "ЛФ", "#edbb00", 14),
    ("Консервативная лига", "КЛ", "#2d2b2b", 12),
    ("Движение «Заря»", "ДЗ", "#b3541e", 9),
    ("Независимые", "НЗ", "#7d7979", 6),
]


class AppTestCase(unittest.TestCase):
    """Приложение на временном файле проекта."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "игра.parlament.json"

        self.service = ParlamentService(self.path)
        self.service.bootstrap()
        self.page = FakePage()
        self.app = ParlamentApp(self.page, self.service)
        self.app.build()

    # -- помощники ----------------------------------------------------------

    def add_parties(self, count: int = 7) -> None:
        for name, abbr, color, _seats in SAMPLE[:count]:
            self.service.create_party(name=name, color=color, abbr=abbr)
        self.app.render()

    def distribute(self, count: int = 7) -> None:
        """Раздаёт места через поля ввода — тем же путём, что и пользователь."""
        self.add_parties(count)
        for (name, _abbr, _color, seats), party in zip(SAMPLE[:count], self.app.parties):
            self.type_seats(party.id, str(seats))

    def type_seats(self, party_id: str, value: str) -> ft.TextField:
        """Имитирует ввод в поле мест: ставит значение и зовёт обработчик."""
        field = self.app.seat_fields[party_id]
        field.value = value
        self.app._on_seat_change(ft.ControlEvent(control=field, name="change", data=value))
        return field

    @property
    def body(self):
        return self.page.controls[0]


class TestAppBar(AppTestCase):
    """Ни электроновского ряда «Файл · Правка · Справка», ни заменившего его
    выпадающего меню «Файл» в шапке нет — проект всегда работает с одним,
    автоматически сохраняемым файлом."""

    def test_no_leftover_menu_bar(self):
        self.assertIsNone(find(self.body, lambda c: isinstance(c, ft.MenuBar)))

    def test_no_file_menu(self):
        self.assertIsNone(find(self.body, lambda c: isinstance(c, ft.PopupMenuButton)))
        self.assertNotIn("Файл", texts(self.body))


class TestFirstRun(AppTestCase):
    def test_starts_with_one_empty_convocation(self):
        self.assertEqual(len(self.app.convocations), 1)
        self.assertEqual(self.app.selected.name, "Первый состав")
        self.assertEqual(self.app.used_seats(self.app.selected), 0)

    def test_empty_state_copy(self):
        shown = texts(self.body)
        self.assertIn("Партий пока нет", shown)
        # Поясняющие абзацы убраны — заголовок и кнопка говорят всё нужное сами.
        self.assertFalse(any("Создайте 3–6 партий" in t for t in shown))
        self.assertFalse(any("Список появится после создания партий" in t for t in shown))

    def test_empty_state_button_opens_parties_screen(self):
        # Кнопка не открывает диалог создания партии, а просто ведёт
        # в справочник — там уже есть кнопка «Новая партия».
        find(self.body, lambda c: isinstance(c, ft.Button)
             and c.content == "Создать первую партию").on_click(None)
        self.assertEqual(self.app.view, "parties")
        self.assertIsNone(self.page.dialog)

    def test_actions_disabled_without_parties(self):
        buttons = find_all(self.body, lambda c: isinstance(c, ft.Button))
        new_convocation = [b for b in buttons if b.content == "Новый созыв"]
        self.assertTrue(new_convocation and new_convocation[0].disabled)

    def test_chart_is_all_grey(self):
        seats = compute_seats(self.app.total_seats, self.service.project.rows, [])
        self.assertEqual(len(seats), 120)
        self.assertTrue(all(s.color == theme.EMPTY_SEAT for s in seats))


class TestSeatDistribution(AppTestCase):
    def test_typing_updates_service_and_derived(self):
        self.distribute()
        conv = self.app.selected
        self.assertEqual(self.app.used_seats(conv), 120)
        self.assertEqual(conv.seats[self.app.parties[0].id], 34)
        self.assertEqual(self.app.used_text.value, "120 / 120")
        self.assertEqual(self.app.remaining_text.value, "0")
        self.assertEqual(self.app.remaining_note.value, "Все места распределены.")
        self.assertEqual(self.app.progress.value, 1.0)

    def test_remainder_is_reported(self):
        self.add_parties(2)
        self.type_seats(self.app.parties[0].id, "50")
        self.assertEqual(self.app.remaining_text.value, "70")
        self.assertEqual(self.app.remaining_text.color, theme.ACCENT_2_700)
        self.assertIn("Осталось распределить 70 мест", self.app.remaining_note.value)

    def test_input_is_clamped_to_total(self):
        self.distribute()
        # Все 120 мест розданы: соседям принадлежит 86, значит потолок — 34.
        field = self.type_seats(self.app.parties[0].id, "999")
        self.assertEqual(field.value, "34")
        self.assertEqual(self.app.used_seats(self.app.selected), 120)

    def test_negative_becomes_zero(self):
        self.add_parties(2)
        field = self.type_seats(self.app.parties[0].id, "-5")
        self.assertEqual(field.value, "0")
        self.assertEqual(self.app.used_seats(self.app.selected), 0)

    def test_letters_are_rejected(self):
        self.add_parties(2)
        self.type_seats(self.app.parties[0].id, "40")
        field = self.type_seats(self.app.parties[0].id, "сорок")
        self.assertEqual(field.value, "40")           # вернулось прежнее значение
        self.assertEqual(self.app.used_seats(self.app.selected), 40)

    def test_blank_field_becomes_zero_on_blur(self):
        self.add_parties(2)
        party_id = self.app.parties[0].id
        self.type_seats(party_id, "40")
        field = self.app.seat_fields[party_id]
        field.value = ""
        self.app._on_seat_blur(ft.ControlEvent(control=field, name="blur", data=""))
        self.assertEqual(field.value, "0")
        self.assertEqual(self.app.used_seats(self.app.selected), 0)

    def test_legend_and_percentages(self):
        self.distribute()
        shown = texts(self.app.legend_row)
        self.assertIn("Народный союз", shown)
        self.assertIn("28,3 %", shown)
        self.assertIn("5,0 %", shown)

    def test_largest_party_leads_the_chart(self):
        self.distribute()
        distribution = self.app.distribution(self.app.selected)
        self.assertEqual([seats for _p, seats in distribution], [34, 27, 18, 14, 12, 9, 6])

    def test_majority_notes(self):
        self.add_parties(2)
        self.type_seats(self.app.parties[0].id, "60")
        self.assertIn("большинства нет", self.app.majority_text.value)
        self.assertEqual(self.app.majority_text.color, theme.NEUTRAL_700)

        self.type_seats(self.app.parties[0].id, "61")
        self.assertEqual(self.app.majority_text.value,
                         "Народный союз — абсолютное большинство")
        self.assertEqual(self.app.majority_text.color, theme.ACCENT_2_700)

    def test_reset_clears_everything(self):
        self.distribute()
        self.app.reset_seats()
        confirm = find(self.page.dialog, lambda c: isinstance(c, ft.Button)
                       and c.content == "Сбросить")
        confirm.on_click(None)
        self.assertEqual(self.app.used_seats(self.app.selected), 0)
        self.assertEqual(self.page.last_toast, "Распределение сброшено.")


class TestParties(AppTestCase):
    def test_create_through_dialog(self):
        self.app.new_party()
        dialog = self.page.dialog
        fields = find_all(dialog, lambda c: isinstance(c, ft.TextField))
        fields[0].value = "Народный союз"
        fields[1].value = "#0088B0"

        find(dialog, lambda c: isinstance(c, ft.Button) and c.content == "Сохранить").on_click(None)

        self.assertEqual(len(self.app.parties), 1)
        self.assertEqual(self.app.parties[0].name, "Народный союз")
        self.assertEqual(self.app.parties[0].color, "#0088b0")
        self.assertEqual(self.page.last_toast, "Партия «Народный союз» создана.")

    def test_blank_name_keeps_dialog_open(self):
        self.app.new_party()
        dialog = self.page.dialog
        find(dialog, lambda c: isinstance(c, ft.Button) and c.content == "Сохранить").on_click(None)

        self.assertEqual(len(self.app.parties), 0)
        self.assertIs(self.page.dialog, dialog)      # диалог не закрылся
        error = find(dialog, lambda c: isinstance(c, ft.Text)
                     and c.value == "Введите название партии.")
        self.assertIsNotNone(error)

    def test_bad_hex_keeps_dialog_open(self):
        self.app.new_party()
        dialog = self.page.dialog
        fields = find_all(dialog, lambda c: isinstance(c, ft.TextField))
        fields[0].value = "Партия"
        fields[1].value = "синий"
        find(dialog, lambda c: isinstance(c, ft.Button) and c.content == "Сохранить").on_click(None)

        self.assertEqual(len(self.app.parties), 0)
        self.assertIsNotNone(find(dialog, lambda c: isinstance(c, ft.Text)
                                  and "3A7CA5" in str(c.value)))

    def test_edit_propagates_to_chart_and_legend(self):
        self.distribute()
        party = self.app.parties[0]
        self.app.edit_party(party)
        dialog = self.page.dialog
        fields = find_all(dialog, lambda c: isinstance(c, ft.TextField))
        fields[0].value = "Новый союз"
        fields[1].value = "#5B4B8A"
        find(dialog, lambda c: isinstance(c, ft.Button) and c.content == "Сохранить").on_click(None)

        updated = self.service.project.party(party.id)
        self.assertEqual(updated.name, "Новый союз")
        self.assertEqual(updated.color, "#5b4b8a")
        # Места остались за той же партией.
        self.assertEqual(self.app.selected.seats[party.id], 34)
        self.assertIn("Новый союз", texts(self.app.legend_row))

    def test_delete_dialog_lists_affected_convocations(self):
        self.distribute()
        self.app.new_convocation()
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Зафиксировать").on_click(None)

        party = self.app.parties[6]        # «Независимые», 6 мест в первом составе
        self.app.delete_party(party)
        shown = texts(self.page.dialog)
        self.assertTrue(any("Удалить «Независимые»?" in t for t in shown))
        self.assertTrue(any("Первый состав — 6 мест" in t for t in shown))

    def test_delete_frees_seats_everywhere(self):
        self.distribute()
        party = self.app.parties[6]
        self.app.delete_party(party)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertIsNone(self.service.project.party(party.id))
        self.assertNotIn(party.id, self.app.selected.seats)
        self.assertEqual(self.app.used_seats(self.app.selected), 114)

    def test_directory_screen_shows_table(self):
        self.distribute()
        self.app.show_parties()
        shown = texts(self.body)
        self.assertIn("7 партий", shown)
        self.assertIn("Народный союз", shown)
        self.assertIn("#0088B0", shown)          # HEX в таблице

    def test_directory_counts_convocations(self):
        self.distribute()
        self.app.show_parties()
        view_rows = find_all(self.body, lambda c: isinstance(c, ft.DataRow))
        self.assertEqual(len(view_rows), 7)


class TestConvocations(AppTestCase):
    def fix_current(self, name: str | None = None) -> None:
        self.app.new_convocation()
        if name is not None:
            field = find(self.page.dialog, lambda c: isinstance(c, ft.TextField))
            field.value = name
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Зафиксировать").on_click(None)

    def test_dialog_summarises_current_state(self):
        self.distribute()
        self.app.new_convocation()
        shown = texts(self.page.dialog)
        self.assertTrue(any("Зафиксировать Первый состав?" in t for t in shown))
        self.assertTrue(any("120 из 120 мест распределены · 7 партий" in t for t in shown))
        field = find(self.page.dialog, lambda c: isinstance(c, ft.TextField))
        self.assertEqual(field.value, "Второй состав")

    def test_fixing_archives_and_opens_next(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()

        self.assertEqual(len(self.app.convocations), 2)
        self.assertTrue(self.service.project.convocation(first_id).is_fixed)
        self.assertEqual(self.app.selected.name, "Второй состав")
        self.assertEqual(self.app.used_seats(self.app.selected), 0)
        self.assertEqual(self.page.last_toast, "Созыв зафиксирован, открыт новый состав.")

    def test_custom_name(self):
        self.distribute()
        self.fix_current("Второй состав (кризисные выборы)")
        self.assertEqual(self.app.selected.name, "Второй состав (кризисные выборы)")

    def test_archived_is_read_only_until_asked(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()

        self.app.select_convocation(first_id)
        self.assertFalse(self.app.is_editable)
        shown = texts(self.body)
        self.assertIn("СОСТАВ — ТОЛЬКО ЧТЕНИЕ", shown)
        self.assertIn("Просмотр истории", shown)

        self.app.edit_archived()
        self.assertTrue(self.app.is_editable)
        self.assertIn("РАСПРЕДЕЛЕНИЕ МЕСТ", texts(self.body))

    def test_editing_history_does_not_spawn_convocation(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        self.app.select_convocation(first_id)
        self.app.edit_archived()

        # Освобождаем места у соседа и отдаём их первой партии.
        self.type_seats(self.app.parties[1].id, "21")
        self.type_seats(self.app.parties[0].id, "40")

        self.assertEqual(len(self.app.convocations), 2)
        self.assertEqual(self.app.selected.id, first_id)
        self.assertEqual(self.service.project.convocation(first_id).seats[self.app.parties[0].id], 40)

    def test_rename(self):
        self.distribute()
        self.app.rename_convocation()
        field = find(self.page.dialog, lambda c: isinstance(c, ft.TextField))
        field.value = "Первый состав (учредительный)"
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Сохранить").on_click(None)
        self.assertEqual(self.app.selected.name, "Первый состав (учредительный)")

    def test_convocation_cards(self):
        self.distribute()
        self.fix_current()
        shown = texts(self.app.conv_list)
        self.assertIn("Второй состав", shown)
        self.assertIn("Первый состав", shown)
        self.assertIn("Редактируется", shown)
        self.assertIn("120 из 120 мест", shown)
        # Дат и времени фиксации в карточках больше нет.
        self.assertNotIn("сейчас", shown)

    def test_stage_header_has_no_fixed_date(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        self.app.select_convocation(first_id)
        self.assertFalse(any("зафиксирован" in t for t in texts(self.body)))

    def test_new_convocation_needs_parties(self):
        self.app.new_convocation()
        self.assertEqual(self.page.last_toast, "Сначала создайте хотя бы одну партию.")


class TestExport(AppTestCase):
    def test_dialog_defaults(self):
        self.distribute()
        self.app.export_png()
        field = find(self.page.dialog, lambda c: isinstance(c, ft.TextField))
        self.assertEqual(field.value, "Парламент_Первый_состав.png")
        radios = find_all(self.page.dialog, lambda c: isinstance(c, ft.Radio))
        self.assertEqual([r.label for r in radios],
                         ["1920 × 1080", "2560 × 1440", "3840 × 2160"])

    def test_export_refuses_empty_distribution(self):
        self.add_parties(3)
        self.app.export_png()
        self.assertEqual(self.page.last_toast,
                         "Нечего экспортировать: места не распределены.")

    def test_png_is_produced_at_requested_size(self):
        entries = [LegendEntry(name, color, seats)
                   for name, _abbr, color, seats in SAMPLE]
        for width, height in ((1920, 1080), (3840, 2160)):
            data = render_png(entries, width=width, height=height,
                              title="Первый состав", with_title=True)
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertEqual(int.from_bytes(data[16:20], "big"), width)
            self.assertEqual(int.from_bytes(data[20:24], "big"), height)
            self.assertGreater(len(data), 10_000)

    def test_save_flow_hands_png_bytes_to_the_picker(self):
        """Проверяет связку целиком: диалог → отрисовка → системное сохранение."""
        self.distribute()

        calls = []

        class StubPicker:
            async def save_file(self, **kwargs):
                calls.append(kwargs)
                return "/куда-то/Парламент_Первый_состав.png"

        self.app.file_picker = StubPicker()
        self.app.export_png()
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Сохранить как…").on_click(None)

        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["file_name"], "Парламент_Первый_состав.png")
        self.assertEqual(call["allowed_extensions"], ["png"])
        self.assertTrue(call["src_bytes"].startswith(b"\x89PNG"))
        self.assertEqual(int.from_bytes(call["src_bytes"][16:20], "big"), 1920)
        self.assertEqual(self.page.last_toast, "Картинка сохранена.")

    def test_save_flow_adds_missing_extension(self):
        self.distribute()

        calls = []

        class StubPicker:
            async def save_file(self, **kwargs):
                calls.append(kwargs)
                return None                      # пользователь отменил сохранение

        self.app.file_picker = StubPicker()
        self.app.export_png()
        field = find(self.page.dialog, lambda c: isinstance(c, ft.TextField))
        field.value = "мой-парламент"
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Сохранить как…").on_click(None)

        self.assertEqual(calls[0]["file_name"], "мой-парламент.png")

    def test_file_name_template(self):
        self.assertEqual(suggest_file_name("Третий состав"), "Парламент_Третий_состав.png")
        # Символы, запрещённые в именах файлов Windows, выкидываем.
        self.assertEqual(suggest_file_name('Состав: "А/Б"'), "Парламент_Состав_АБ.png")


class TestProjectFile(AppTestCase):
    def test_every_change_is_saved(self):
        self.distribute()
        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(len(again.project.parties), 7)
        self.assertEqual(sum(again.project.active_convocation.seats.values()), 120)

    def test_keeps_saving_to_whatever_file_the_service_points_at(self):
        """У интерфейса нет своего меню «Файл» — за выбор файла целиком
        отвечает `service`, а окно просто отражает то, что в нём открыто."""
        self.distribute()
        other = self.path.parent / "вторая-игра.parlament.json"
        self.service.save_project_as(other)
        self.app.render()

        self.type_seats(self.app.parties[0].id, "30")
        reopened = ParlamentService(other)
        reopened.bootstrap()
        self.assertEqual(reopened.project.active_convocation.seats[self.app.parties[0].id], 30)


class TestHelpers(unittest.TestCase):
    def test_hex_normalisation(self):
        self.assertEqual(normalize_hex("3A7CA5"), "#3a7ca5")
        self.assertEqual(normalize_hex("#ABC"), "#aabbcc")
        self.assertIsNone(normalize_hex("не цвет"))
        self.assertIsNone(normalize_hex(""))

    def test_seat_geometry_matches_design(self):
        seats = compute_seats(120, 5, [("#0088b0", 34), ("#d6006c", 27)])
        self.assertEqual(len(seats), 120)
        self.assertEqual(sum(1 for s in seats if s.color == "#0088b0"), 34)
        self.assertEqual(sum(1 for s in seats if s.color == theme.EMPTY_SEAT), 59)
        # Полукруг симметричен относительно центра поля 630.
        self.assertAlmostEqual(seats[0].x + seats[-1].x, 630, delta=0.5)

    def test_row_counts_sum_to_total(self):
        for total in (60, 120, 300):
            for rows in (3, 5, 8):
                self.assertEqual(len(compute_seats(total, rows, [])), total)


if __name__ == "__main__":
    unittest.main(verbosity=2)
