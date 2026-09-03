"""Тесты интерфейса: сценарии ТЗ, прогнанные через настоящий `ParlamentApp`.

Приложение собирается на подставной странице (`FakePage`), поэтому проверки
идут без запуска окна, но через тот же код, что работает у пользователя:
те же обработчики полей, те же диалоги, тот же `service`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flet as ft  # noqa: E402

from fake_page import FakePage, find, find_all, texts  # noqa: E402
from parlament.service import ParlamentService, ValidationError  # noqa: E402
from parlament.ui import theme  # noqa: E402
from parlament.ui.app import ParlamentApp  # noqa: E402
from parlament.ui.dialogs import normalize_hex  # noqa: E402
from parlament.ui.export import LegendEntry, render_png, suggest_file_name  # noqa: E402
from parlament.district_geometry import (  # noqa: E402
    DISTRICT_CENTRES,
    DISTRICT_SHAPES,
    MAP_ASPECT,
)
from parlament.ui.map_export import render_map_png  # noqa: E402
from parlament.ui.seat_chart import compute_seats  # noqa: E402

#: Пример раскладки: суммой ровно на полный парламент (147 мест по карте).
SAMPLE = [
    ("Народный союз", "НС", "#0088b0", 42),
    ("Партия труда", "ПТ", "#d6006c", 33),
    ("Аграрный блок", "АБ", "#4c7a34", 22),
    ("Либеральный форум", "ЛФ", "#edbb00", 17),
    ("Консервативная лига", "КЛ", "#2d2b2b", 15),
    ("Движение «Заря»", "ДЗ", "#b3541e", 11),
    ("Независимые", "НЗ", "#7d7979", 7),
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

    def test_no_duplicate_export_and_new_convocation_buttons(self):
        # «Экспорт в PNG» и «Новый созыв» в шапке дублировали кнопки в правой
        # и левой панелях — оставлены только там.
        self.add_parties()
        buttons = find_all(self.body, lambda c: isinstance(c, ft.Button))
        self.assertEqual([b for b in buttons if b.content == "Экспорт в PNG"], [])
        self.assertEqual([b for b in buttons if b.content == "Новый созыв"], [])
        self.assertTrue(any(b.content == "Экспортировать в PNG" for b in buttons))
        self.assertTrue(any(b.content == "+ Новый созыв" for b in buttons))


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
        new_convocation = [b for b in buttons if b.content == "+ Новый созыв"]
        self.assertTrue(new_convocation and new_convocation[0].disabled)

    def test_chart_is_all_grey(self):
        seats = compute_seats(self.app.total_seats, self.service.project.rows, [])
        self.assertEqual(len(seats), self.app.total_seats)
        self.assertTrue(all(s.color == theme.EMPTY_SEAT for s in seats))


class TestSeatDistribution(AppTestCase):
    def test_typing_updates_service_and_derived(self):
        self.distribute()
        conv = self.app.selected
        self.assertEqual(self.app.used_seats(conv), 147)
        self.assertEqual(conv.seats[self.app.parties[0].id], 42)
        self.assertEqual(self.app.used_text.value, "147 / 147")
        self.assertEqual(self.app.remaining_text.value, "0")
        self.assertEqual(self.app.remaining_note.value, "Все места распределены.")
        self.assertEqual(self.app.progress.value, 1.0)

    def test_remainder_is_reported(self):
        self.add_parties(2)
        self.type_seats(self.app.parties[0].id, "50")
        self.assertEqual(self.app.remaining_text.value, "97")
        self.assertEqual(self.app.remaining_text.color, theme.ACCENT_2_700)
        self.assertIn("Осталось распределить 97 мест", self.app.remaining_note.value)

    def test_input_is_clamped_to_total(self):
        self.distribute()
        # Все 147 мест розданы: соседям принадлежит 105, значит потолок — 42.
        field = self.type_seats(self.app.parties[0].id, "999")
        self.assertEqual(field.value, "42")
        self.assertEqual(self.app.used_seats(self.app.selected), 147)

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
        self.assertIn("28,6 %", shown)
        self.assertIn("4,8 %", shown)

    def test_largest_party_leads_the_chart(self):
        self.distribute()
        distribution = self.app.distribution(self.app.selected)
        self.assertEqual([seats for _p, seats in distribution], [42, 33, 22, 17, 15, 11, 7])

    def test_majority_notes(self):
        self.add_parties(2)
        majority = self.app.majority_seats          # 74 из 147
        self.type_seats(self.app.parties[0].id, str(majority - 1))
        self.assertIn("большинства нет", self.app.majority_text.value)
        self.assertEqual(self.app.majority_text.color, theme.NEUTRAL_700)

        self.type_seats(self.app.parties[0].id, str(majority))
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
        self.assertEqual(self.app.selected.seats[party.id], 42)
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
        self.assertTrue(any("Первый состав — 7 мест" in t for t in shown))

    def test_delete_frees_seats_everywhere(self):
        self.distribute()
        party = self.app.parties[6]
        self.app.delete_party(party)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertIsNone(self.service.project.party(party.id))
        self.assertNotIn(party.id, self.app.selected.seats)
        self.assertEqual(self.app.used_seats(self.app.selected), 140)

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


class _FakePoint:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeTapEvent:
    """Заменяет TapEvent Flet — тесты не поднимают настоящий жест мыши,
    только вызывают обработчик напрямую с координатами клика."""

    def __init__(self, x, y):
        self.local_position = _FakePoint(x, y)


class TestRecentColors(AppTestCase):
    """Свои цвета, подобранные вручную, предлагаются в следующих диалогах —
    отдельной строкой под основной палитрой."""

    def test_no_recent_row_until_something_was_picked(self):
        self.assertEqual(self.app.recent_colors, [])
        self.app.new_party()
        divider = find(self.page.dialog, lambda c: isinstance(c, ft.Container)
                       and c.bgcolor == theme.DIVIDER and c.height == 1)
        self.assertIsNone(divider)

    def test_picking_custom_color_is_remembered_and_offered_next_time(self):
        self.app.new_party()
        plus = find(self.page.dialog, lambda c: isinstance(c, ft.Container)
                   and getattr(c, "tooltip", None) == "Свой цвет")
        plus.on_click(None)

        picker = self.page.dialog
        sv_gesture, hue_gesture = find_all(picker, lambda c: isinstance(c, ft.GestureDetector))
        hue_gesture.on_tap_down(_FakeTapEvent(20, 0))     # тон ближе к красному
        sv_gesture.on_tap_down(_FakeTapEvent(120, 60))    # неполная насыщенность/яркость
        find(picker, lambda c: isinstance(c, ft.Button) and c.content == "Выбрать").on_click(None)

        self.assertEqual(len(self.app.recent_colors), 1)
        remembered = self.app.recent_colors[0]
        self.assertRegex(remembered, r"^#[0-9a-f]{6}$")

        # Значение уже перенеслось и в само поле HEX основного диалога.
        self.assertEqual(find(self.page.dialog, lambda c: isinstance(c, ft.TextField)
                              and c.label == "HEX").value, remembered.upper())

        # Диалог партии закрыт (отменяем) и открыт заново — цвет предложен готовым.
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Отмена").on_click(None)
        self.app.new_party()
        swatch = find(self.page.dialog, lambda c: isinstance(c, ft.Container)
                     and c.data == remembered)
        self.assertIsNotNone(swatch)

    def test_recent_colors_are_capped_deduplicated_and_most_recent_first(self):
        colors = [f"#{n:02x}{n:02x}{n:02x}" for n in range(1, 12)]   # 11 цветов
        for color in colors:
            self.app._remember_recent_color(color)
        # Хранится 10 последних — самый первый (#010101) вытеснен.
        self.assertEqual(self.app.recent_colors, list(reversed(colors[1:])))

        self.app._remember_recent_color(colors[5])   # уже есть — переезжает вперёд
        self.assertEqual(self.app.recent_colors[0], colors[5])
        self.assertEqual(len(self.app.recent_colors), 10)

    def test_palette_colors_are_not_remembered(self):
        self.app._remember_recent_color(theme.PALETTE[0])
        self.assertEqual(self.app.recent_colors, [])

    def test_recent_colors_survive_app_restart(self):
        self.app._remember_recent_color("#a1b2c3")

        again_service = ParlamentService(self.path)
        again_service.bootstrap()
        again_app = ParlamentApp(FakePage(), again_service)
        again_app.build()

        self.assertEqual(again_app.recent_colors, ["#a1b2c3"])


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
        self.assertTrue(any("147 из 147 мест распределены · 7 партий" in t for t in shown))
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
        self.assertIn("147 из 147 мест", shown)
        # Дат и времени фиксации в карточках больше нет.
        self.assertNotIn("сейчас", shown)

    def test_stage_header_has_no_fixed_date(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        self.app.select_convocation(first_id)
        self.assertFalse(any("зафиксирован" in t for t in texts(self.body)))

    def test_no_delete_button_with_a_single_convocation(self):
        # Единственный созыв удалить нельзя — история не может опустеть,
        # и кнопке тогда просто нечего делать.
        self.assertIsNone(find(self.app.conv_list, lambda c: isinstance(c, ft.IconButton)))

    def test_delete_button_appears_with_more_than_one(self):
        self.distribute()
        self.fix_current()
        buttons = find_all(self.app.conv_list, lambda c: isinstance(c, ft.IconButton))
        self.assertEqual(len(buttons), 2)

    def test_delete_archived_convocation(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        second_id = self.app.selected.id

        first = self.service.project.convocation(first_id)
        self.app.delete_convocation(first)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertEqual(len(self.app.convocations), 1)
        self.assertIsNone(self.service.project.convocation(first_id))
        # Текущий (не удалявшийся) созыв остаётся выбранным как был.
        self.assertEqual(self.app.selected.id, second_id)
        self.assertEqual(self.page.last_toast, "Созыв «Первый состав» удалён.")

    def test_deleting_viewed_convocation_switches_selection(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        self.app.select_convocation(first_id)   # смотрим именно тот, что удалим

        first = self.service.project.convocation(first_id)
        self.app.delete_convocation(first)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertNotEqual(self.app.selected_convocation_id, first_id)
        self.assertEqual(self.app.selected.id, self.service.project.active_convocation.id)

    def test_deleting_active_convocation_reopens_previous_for_editing(self):
        self.distribute()
        first_id = self.app.selected.id
        self.fix_current()
        active = self.service.project.active_convocation

        self.app.delete_convocation(active)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertEqual(self.app.selected.id, first_id)
        self.assertTrue(self.app.is_editable)

    def test_cannot_delete_the_last_convocation(self):
        conv = self.app.selected
        self.app.delete_convocation(conv)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Удалить всё равно").on_click(None)

        self.assertEqual(self.page.last_toast, "Нельзя удалить единственный созыв.")
        self.assertEqual(len(self.app.convocations), 1)

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
        self.assertEqual(sum(again.project.active_convocation.seats.values()), 147)

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


class TestMapAndElections(AppTestCase):
    """Карта и выборы: навигация, модификаторы, розыгрыш, раскраска."""

    def setUp(self):
        super().setUp()
        self.add_parties(3)
        self.by_name = {d.name: d for d in self.service.project.districts}

    def support(self, district_name: str, party_index: int, points: int,
                settlements: int = 1):
        """Даёт партии очки популярности в округе — так она попадает в выборы."""
        district = self.by_name[district_name]
        made = [self.service.add_settlement(district.id, f"НП {i + 1}")
                for i in range(settlements)]
        self.service.set_support(district.id, made[0].id,
                                 self.app.parties[party_index].id, points)
        return district

    def bonus(self, district_name: str, party_index: int, value: str):
        """Вписывает бонус за дебаты так же, как это делает человек."""
        district = self.by_name[district_name]
        field, _button = self.app.elections.cells[district.id][
            self.app.parties[party_index].id]
        field.value = value
        field.on_change(ft.ControlEvent(control=field, name="change", data=value))
        return field

    def agitate(self, district_name: str, party_index: int):
        district = self.by_name[district_name]
        _field, button = self.app.elections.cells[district.id][
            self.app.parties[party_index].id]
        button.on_click(ft.ControlEvent(control=button, name="click", data=""))
        return button

    def test_map_is_reachable_from_parliament(self):
        button = find(self.body, lambda c: isinstance(c, ft.Button) and c.content == "Карта")
        self.assertIsNotNone(button)
        button.on_click(None)
        self.assertEqual(self.app.view, "map")

    def test_map_says_when_there_were_no_elections(self):
        self.app.show_map()
        self.assertTrue(any("выборы не проводились" in t for t in texts(self.body)))

    def test_export_is_locked_until_there_are_results(self):
        self.app.show_map()
        export = find(self.body, lambda c: isinstance(c, ft.Button)
                      and c.content == "Экспорт карты в PNG")
        self.assertTrue(export.disabled)

    def test_support_screen_is_reachable_from_the_map(self):
        self.app.show_map()
        button = find(self.body, lambda c: isinstance(c, ft.Button)
                      and c.content == "Поддержка")
        self.assertIsNotNone(button)
        button.on_click(None)
        self.assertEqual(self.app.view, "support")

    def test_election_fills_the_parliament_and_colours_the_map(self):
        district = self.support("Гаффинсвик центр", 0, 4)
        self.app.show_elections()
        self.bonus("Гаффинсвик центр", 1, "3")
        self.app.apply_election()

        conv = self.app.selected
        self.assertEqual(self.app.view, "map")          # сразу показали карту
        self.assertEqual(sum(conv.seats.values()), district.seats)
        self.assertIn(district.id, self.service.district_winners(conv.id))

    def test_districts_are_coloured_by_winner(self):
        self.support("Судбригг", 2, 6)
        self.app.show_elections()
        self.app.apply_election()

        painted = {name: color for _code, name, _s, color in self.app.map_chart._districts}
        winner = self.service.district_winners(self.app.selected.id)[
            self.by_name["Судбригг"].id]
        self.assertEqual(painted["Судбригг"],
                         next(p.color for p in self.app.parties if p.id == winner))
        self.assertIsNone(painted["Гаффинсвик центр"])   # без данных — серый

    def test_every_district_has_a_shape_to_draw(self):
        # Карта рисуется полигонами: округ без геометрии остался бы дырой.
        self.app.show_map()
        for code, _name, _seats, _color in self.app.map_chart._districts:
            self.assertIn(code, DISTRICT_SHAPES)

    def test_click_inside_a_district_opens_it(self):
        self.app.show_map()
        chart = self.app.map_chart
        chart._rect = (0.0, 0.0, 1000.0, 1000.0 / MAP_ASPECT)
        chart._width_px, chart._height_px = 1000.0, 1000.0 / MAP_ASPECT

        target = self.by_name["Судбригг"]
        cx, cy = DISTRICT_CENTRES[target.code]
        chart._on_tap(_FakeTapEvent(cx * 1000.0, cy * (1000.0 / MAP_ASPECT)))
        self.assertIn("Судбригг", texts(self.page.dialog))

    def test_agitation_toggles_and_reaches_the_roll(self):
        self.app.show_elections()
        button = self.agitate("Судбригг", 0)
        self.assertEqual(button.icon_color, theme.ACCENT)

        self.app.apply_election()
        roll = self.app.selected.rolls[self.by_name["Судбригг"].id][
            self.app.parties[0].id]
        self.assertTrue(roll.agitation)

    def test_negative_debate_bonus_is_allowed(self):
        self.support("Судбригг", 0, 3)
        self.app.show_elections()
        self.bonus("Судбригг", 0, "-2")
        self.app.apply_election()
        self.assertEqual(
            self.app.selected.rolls[self.by_name["Судбригг"].id][
                self.app.parties[0].id].debate, -2)

    def test_letters_never_reach_the_bonus_field(self):
        self.app.show_elections()
        field = self.bonus("Судбригг", 0, "-1абв2")
        self.assertEqual(field.value, "-12")

    def test_preview_shows_support_not_a_promised_result(self):
        # Обещать итог до броска нельзя: бросок случаен.
        self.support("Гаффинсвик центр", 0, 6, settlements=2)
        self.app.show_elections()
        preview = self.app.elections.previews[self.by_name["Гаффинсвик центр"].id]
        self.assertIn("3,0", preview.value)          # 6 очков на 2 пункта

    def test_district_with_nobody_running_says_so(self):
        self.app.show_elections()
        preview = self.app.elections.previews[self.by_name["Судбригг"].id]
        self.assertEqual(preview.value, "никто не идёт")

    def test_election_without_any_setup_is_refused(self):
        self.app.show_elections()
        self.app.apply_election()
        self.assertIn("ни поддержки, ни модификаторов", self.page.last_toast)
        self.assertEqual(self.app.view, "elections")   # с экрана не увели

    def test_reopening_shows_previous_modifiers(self):
        self.support("Судбригг", 0, 3)
        self.app.show_elections()
        self.bonus("Судбригг", 0, "2")
        self.agitate("Судбригг", 0)
        self.app.apply_election()

        self.app.show_elections()
        district = self.by_name["Судбригг"].id
        field, button = self.app.elections.cells[district][self.app.parties[0].id]
        self.assertEqual(field.value, "2")
        self.assertEqual(button.icon_color, theme.ACCENT)

    def test_district_dialog_shows_the_breakdown(self):
        self.support("Гаффинсвик центр", 0, 4)
        self.app.show_elections()
        self.bonus("Гаффинсвик центр", 0, "2")
        self.app.apply_election()

        self.app.show_district(self.by_name["Гаффинсвик центр"].id)
        shown = " ".join(texts(self.page.dialog))
        self.assertIn("=", shown)          # «4 + 4,0 + 2 = 10,0»
        self.assertIn("%", shown)

    def test_map_png_renders_without_any_background(self):
        # Подложка необязательна: карта рисуется границами округов.
        self.support("Судбригг", 0, 4)
        self.app.show_elections()
        self.app.apply_election()
        data = render_map_png(
            [(d.code, d.name, d.seats, None) for d in self.service.project.districts],
            width=640, title="Первый состав",
            legend=[("Народный союз", "#0088b0", 1, 2)],
        )
        self.assertTrue(data.startswith(b"\x89PNG"))


class TestSeatsAfterElection(AppTestCase):
    """Зал после выборов: места производные и руками не правятся."""

    def setUp(self):
        super().setUp()
        self.add_parties(3)
        self.by_name = {d.name: d for d in self.service.project.districts}
        self.district = self.by_name["Гаффинсвик центр"]
        settlement = self.service.add_settlement(self.district.id, "НП 1")
        self.service.set_support(self.district.id, settlement.id,
                                 self.app.parties[0].id, 4)
        self.app.show_elections()
        self.app.apply_election()
        self.app.show_parliament()

    def test_seat_fields_are_gone(self):
        # Поле ввода развело бы зал с картой: там те же места уже расписаны
        # по округам.
        self.assertFalse(self.app.manual_seats)
        self.assertTrue(any("СОСТАВ ПО ИТОГАМ ВЫБОРОВ" in t
                            for t in texts(self.body)))
        self.assertEqual(find_all(self.body, lambda c: isinstance(c, ft.TextField)), [])

    def test_rail_says_where_the_seats_came_from(self):
        self.assertTrue(any("Места посчитаны по" in t for t in texts(self.body)))

    def test_resetting_the_election_returns_manual_input(self):
        self.app.reset_election()
        confirm = find(self.page.dialog, lambda c: isinstance(c, ft.Button)
                       and c.content == "Сбросить")
        confirm.on_click(None)

        conv = self.app.selected
        self.assertEqual(conv.seats, {})
        self.assertEqual(conv.rolls, {})
        self.assertTrue(self.app.manual_seats)
        self.assertTrue(self.app.seat_fields)

    def test_support_survives_the_reset(self):
        # Сбрасывается розыгрыш, а не игра: очки популярности копились долго.
        self.app.reset_election()
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Сбросить").on_click(None)
        self.assertEqual(
            self.service.support_modifier(self.district.id,
                                          self.app.parties[0].id), 4.0)

    def test_archived_convocation_keeps_its_results(self):
        seats = dict(self.app.selected.seats)
        self.service.fix_convocation()
        self.app.render()
        # Выбранным остаётся зафиксированный созыв — он и открыт на просмотр.
        self.assertTrue(any("Просмотр истории" in t for t in texts(self.body)))
        self.assertEqual(self.app.selected.seats, seats)
        self.assertFalse(self.app.manual_seats)


class TestSupportScreen(AppTestCase):
    """Экран поддержки: населённые пункты и очки популярности."""

    def setUp(self):
        super().setUp()
        self.add_parties(2)
        self.district = self.service.project.districts[0]
        self.app.show_support()

    def field_for(self, settlement, party_index):
        view = self.app.body.content
        return find(self.app.body, lambda c: isinstance(c, ft.TextField)
                    and isinstance(c.data, tuple) and len(c.data) == 4
                    and c.data[1] == settlement.id
                    and c.data[2] == self.app.parties[party_index].id)

    def test_district_opens_and_offers_to_add_a_settlement(self):
        head = find(self.app.body, lambda c: isinstance(c, ft.Container)
                    and getattr(c, "on_click", None) and c.data is None)
        self.app.support_opened.add(self.district.id)
        self.app.render()
        self.assertIsNotNone(find(self.app.body, lambda c: isinstance(c, ft.Button)
                                  and c.content == "+ Населённый пункт"))

    def test_points_are_saved_as_you_type(self):
        settlement = self.service.add_settlement(self.district.id, "Сандавик")
        self.app.support_opened.add(self.district.id)
        self.app.render()

        field = self.field_for(settlement, 0)
        field.value = "4"
        field.on_change(ft.ControlEvent(control=field, name="change", data="4"))
        self.assertEqual(settlement.support[self.app.parties[0].id], 4)

    def test_overspending_a_settlement_is_refused_and_rolled_back(self):
        settlement = self.service.add_settlement(self.district.id, "Сандавик")
        self.service.set_support(self.district.id, settlement.id,
                                 self.app.parties[0].id, 4)
        self.app.support_opened.add(self.district.id)
        self.app.render()

        field = self.field_for(settlement, 1)
        field.value = "5"
        field.on_change(ft.ControlEvent(control=field, name="change", data="5"))

        self.assertNotIn(self.app.parties[1].id, settlement.support)
        self.assertEqual(field.value, "")        # на экране не осталось лишнего
        self.assertIn("очков популярности", self.page.last_toast)


class TestProjectWithoutDistricts(unittest.TestCase):
    """Проект, начатый до появления карты: до выборов всё равно можно дойти.

    Округа таким проектам не выдаются молча — это меняет размер палаты, —
    но и тупика быть не должно, иначе новая механика им недоступна вовсе.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "старый.parlament.json"
        self.path.write_text(json.dumps({
            "schemaVersion": 1, "totalSeats": 120, "rows": 5,
            "parties": [{"id": "p1", "name": "Народный союз", "color": "#0088b0"}],
            "convocations": [{"id": "c1", "number": 1, "name": "Первый состав",
                              "seats": {"p1": 40}}],
        }, ensure_ascii=False), encoding="utf-8")

        self.service = ParlamentService(self.path)
        self.service.bootstrap()
        self.page = FakePage()
        self.app = ParlamentApp(self.page, self.service)
        self.app.build()

    @property
    def body(self):
        return self.page.controls[0]

    def button(self, caption: str):
        return find(self.body, lambda c: isinstance(c, ft.Button) and c.content == caption)

    def test_old_project_keeps_its_own_size(self):
        self.assertEqual(self.service.project.districts, [])
        self.assertEqual(self.service.project.total_seats, 120)

    def test_map_button_is_shown_even_without_districts(self):
        # Иначе до выборов из такого проекта не добраться вообще никак.
        self.assertIsNotNone(self.button("Карта"))

    def test_map_screen_offers_to_add_districts(self):
        self.button("Карта").on_click(None)
        self.assertIsNotNone(self.button("Взять округа с карты"))

    def test_adopting_districts_opens_the_way_to_elections(self):
        self.button("Карта").on_click(None)
        self.button("Взять округа с карты").on_click(None)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Добавить округа").on_click(None)

        self.assertEqual(len(self.service.project.districts), 27)
        self.assertEqual(self.service.project.total_seats, 147)
        self.assertFalse(self.button("Выборы").disabled)

    def test_adopting_keeps_already_distributed_seats(self):
        self.button("Карта").on_click(None)
        self.button("Взять округа с карты").on_click(None)
        find(self.page.dialog, lambda c: isinstance(c, ft.Button)
             and c.content == "Добавить округа").on_click(None)
        self.assertEqual(self.service.project.convocations[0].seats["p1"], 40)

    def test_dialog_spells_out_the_change_in_size(self):
        self.button("Карта").on_click(None)
        self.button("Взять округа с карты").on_click(None)
        self.assertTrue(any("120 → 147" in t for t in texts(self.page.dialog)))

    def test_districts_are_not_adopted_twice(self):
        self.service.adopt_map_districts()
        with self.assertRaises(ValidationError):
            self.service.adopt_map_districts()

    def test_adoption_survives_restart(self):
        self.service.adopt_map_districts()
        again = ParlamentService(self.path)
        again.bootstrap()
        self.assertEqual(len(again.project.districts), 27)
        self.assertEqual(again.project.total_seats, 147)


if __name__ == "__main__":
    unittest.main(verbosity=2)
