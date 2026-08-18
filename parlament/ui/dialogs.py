"""Диалоги: партия, удаление, фиксация созыва, переименование, экспорт, справка.

Каждая функция собирает готовый `ft.AlertDialog` и получает обратный вызов,
который применяет результат. Проверка ввода — здесь же, чтобы пользователь
видел ошибку рядом с полем, а не в уведомлении внизу экрана; окончательную
проверку всё равно делает `service`.
"""

from __future__ import annotations

import re
from typing import Callable

import flet as ft

from ..model import Convocation, Party
from . import theme
from .mount import push
from .export import RESOLUTIONS, suggest_file_name
from .seat_chart import SeatChart

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_hex(value: str) -> str | None:
    """Приводит ввод к `#rrggbb`; None, если это не цвет."""
    text = (value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = "#" + text
    if re.match(r"^#[0-9a-fA-F]{3}$", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    return text.lower() if _HEX.match(text) else None


def _shell(title: str, body: list[ft.Control], actions: list[ft.Control],
           width: int = 460) -> ft.AlertDialog:
    return ft.AlertDialog(
        # Не modal: тогда диалог закрывается по Esc и клику мимо окна —
        # на десктопе этого ждут, и так же вёл себя макет.
        modal=False,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=4),
        title=ft.Text(title, size=20, font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
        content=ft.Container(
            width=width,
            content=ft.Column(body, spacing=15, tight=True),
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _cancel(on_cancel: Callable) -> ft.Control:
    return theme.secondary_button("Отмена", on_cancel)


# -- партия -----------------------------------------------------------------


def party_dialog(party: Party | None, used_colors: int,
                 on_save: Callable[[str, str, str], str | None],
                 on_cancel: Callable) -> ft.AlertDialog:
    """Создание или правка партии.

    `on_save` возвращает текст ошибки либо None, если всё сохранилось.
    """
    editing = party is not None
    start_color = party.color if party else theme.PALETTE[used_colors % len(theme.PALETTE)]

    name_field = theme.text_field(party.name if party else "", label_text="Название",
                                  autofocus=True)
    abbr_field = theme.text_field(party.abbr if party else "", width=150,
                                  label_text="Сокращение")
    hex_field = theme.text_field(start_color.upper(), label_text="Цвет — HEX",
                                 monospace=True)
    preview = theme.swatch(start_color, 14)
    error = ft.Text("", size=12, color=theme.ACCENT_2_700, visible=False)
    palette_row = ft.Row(spacing=7, wrap=True)

    state = {"color": start_color}

    def apply_color(color: str, update_field: bool = True) -> None:
        state["color"] = color
        if update_field:
            hex_field.value = color.upper()
        preview.bgcolor = color
        for control in palette_row.controls:
            if isinstance(control, ft.Container) and control.data:
                selected = control.data == color
                control.border = ft.Border.all(
                    2 if selected else 1,
                    theme.ACCENT if selected else "#1f000000",
                )
        for control in (palette_row, hex_field, preview):
            push(control)

    def on_hex_change(_event) -> None:
        color = normalize_hex(hex_field.value)
        if color:
            apply_color(color, update_field=False)

    hex_field.on_change = on_hex_change

    for color in theme.PALETTE:
        selected = color == start_color.lower()
        palette_row.controls.append(
            ft.Container(
                width=28, height=28, bgcolor=color, data=color,
                border=ft.Border.all(2 if selected else 1,
                                     theme.ACCENT if selected else "#1f000000"),
                tooltip=color,
                on_click=lambda e: apply_color(e.control.data),
            )
        )
    palette_row.controls.append(
        ft.Container(
            content=ft.Text("Своя палитра RGB…", size=13, color=theme.ACCENT_700),
            padding=ft.Padding.only(left=6),
            on_click=lambda _e: _open_rgb_picker(name_field.page, state["color"], apply_color),
        )
    )

    def save(_event) -> None:
        color = normalize_hex(hex_field.value)
        if not (name_field.value or "").strip():
            _show_error(error, "Введите название партии.")
            return
        if not color:
            _show_error(error, "Цвет должен быть в виде #3A7CA5.")
            return
        message = on_save(name_field.value, color, abbr_field.value or "")
        if message:
            _show_error(error, message)

    return _shell(
        "Изменить партию" if editing else "Новая партия",
        [
            name_field,
            ft.Row([abbr_field, ft.Container(hex_field, expand=True)], spacing=12),
            ft.Column([
                ft.Text("Палитра", size=12, color=theme.NEUTRAL_700),
                palette_row,
            ], spacing=5, tight=True),
            ft.Container(
                bgcolor=theme.BG,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Row([
                    preview,
                    ft.Text("Так партия выглядит на схеме и в легенде",
                            size=13, color=theme.NEUTRAL_700),
                ], spacing=10),
            ),
            error,
        ],
        [_cancel(on_cancel), theme.primary_button("Сохранить", save)],
    )


def _show_error(control: ft.Text, message: str) -> None:
    control.value = message
    control.visible = True
    push(control)


def _open_rgb_picker(page: ft.Page, current: str, on_pick: Callable[[str], None]) -> None:
    """Простой подбор цвета тремя ползунками — «своя палитра RGB» из макета."""
    red, green, blue = (int(current[i:i + 2], 16) for i in (1, 3, 5))
    preview = ft.Container(width=56, height=28, bgcolor=current)
    value_text = ft.Text(current.upper(), size=13, font_family="monospace",
                         color=theme.NEUTRAL_700)

    def channels() -> str:
        return "#{:02x}{:02x}{:02x}".format(
            int(sliders[0].value), int(sliders[1].value), int(sliders[2].value)
        )

    def on_slide(_event) -> None:
        color = channels()
        preview.bgcolor = color
        value_text.value = color.upper()
        push(preview)
        push(value_text)

    sliders = [
        ft.Slider(min=0, max=255, divisions=255, value=channel, label=name,
                  active_color=theme.ACCENT, on_change=on_slide)
        for channel, name in ((red, "R"), (green, "G"), (blue, "B"))
    ]

    def close(_event) -> None:
        page.pop_dialog()

    def confirm(_event) -> None:
        on_pick(channels())
        page.pop_dialog()

    page.show_dialog(_shell(
        "Свой цвет",
        [
            ft.Row([preview, value_text], spacing=12),
            *[ft.Row([ft.Text(name, size=13, width=14, color=theme.NEUTRAL_700),
                      ft.Container(slider, expand=True)])
              for name, slider in zip("RGB", sliders)],
        ],
        [_cancel(close), theme.primary_button("Выбрать", confirm)],
        width=360,
    ))


# -- удаление партии --------------------------------------------------------


def delete_party_dialog(party: Party, usage: list[dict], plural: Callable,
                        on_confirm: Callable, on_cancel: Callable) -> ft.AlertDialog:
    count = len(usage)
    if count:
        explanation = (
            f"Партия участвует в {count} "
            f"{plural(count, ['созыве', 'созывах', 'созывах'])}. "
            "После удаления её места в этих составах станут нераспределёнными."
        )
    else:
        explanation = ("Партия не участвует ни в одном созыве — "
                       "удаление ничего не изменит в истории.")

    body: list[ft.Control] = [ft.Text(explanation, size=14, color=theme.TEXT)]
    if usage:
        body.append(ft.Container(
            bgcolor=theme.BG,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=ft.Row([
                theme.swatch(party.color, 12),
                ft.Column([
                    ft.Text(
                        f"{item['convocationName']} — "
                        f"{item['seats']} {plural(item['seats'], ['место', 'места', 'мест'])}",
                        size=13, color=theme.TEXT,
                    )
                    for item in usage
                ], spacing=2, tight=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
        ))

    return _shell(
        f"Удалить «{party.name}»?",
        body,
        [_cancel(on_cancel),
         theme.primary_button("Удалить всё равно", on_confirm, danger=True)],
    )


# -- фиксация созыва --------------------------------------------------------


def new_convocation_dialog(current: Convocation, suggested_name: str, used: int,
                           total: int, party_count: int, segments: list[tuple[str, int]],
                           plural: Callable, on_confirm: Callable[[str], None],
                           on_cancel: Callable) -> ft.AlertDialog:
    name_field = theme.text_field(suggested_name, label_text="Название нового созыва",
                                  autofocus=True)

    return _shell(
        f"Зафиксировать {current.name}?",
        [
            ft.Text(
                f"Текущее распределение сохранится в истории, "
                f"и откроется пустой {suggested_name}.",
                size=14, color=theme.TEXT,
            ),
            ft.Container(
                bgcolor=theme.BG,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Column([
                    seat_bar(segments, height=8),
                    ft.Text(
                        f"{used} из {total} мест распределены · "
                        f"{party_count} {plural(party_count, ['партия', 'партии', 'партий'])}",
                        size=12, color=theme.NEUTRAL_700,
                    ),
                ], spacing=8, tight=True),
            ),
            name_field,
        ],
        [_cancel(on_cancel),
         theme.primary_button("Зафиксировать",
                              lambda _e: on_confirm(name_field.value))],
    )


# -- переименование ---------------------------------------------------------


def rename_dialog(convocation: Convocation, on_confirm: Callable[[str], None],
                  on_cancel: Callable) -> ft.AlertDialog:
    field = theme.text_field(convocation.name, label_text="Название", autofocus=True)
    return _shell(
        "Переименовать созыв",
        [
            ft.Text("Например: «Третий состав (кризисные выборы)».",
                    size=14, color=theme.NEUTRAL_700),
            field,
        ],
        [_cancel(on_cancel),
         theme.primary_button("Сохранить", lambda _e: on_confirm(field.value))],
        width=440,
    )


# -- экспорт ----------------------------------------------------------------


def export_dialog(convocation: Convocation, total: int, rows: int,
                  distribution: list[tuple[str, str, int]],
                  on_confirm: Callable[[dict], None],
                  on_cancel: Callable) -> ft.AlertDialog:
    """`distribution` — тройки «сокращение, цвет, мест» для предпросмотра."""
    file_field = theme.text_field(suggest_file_name(convocation.name),
                                  label_text="Имя файла")

    resolution_picker = ft.RadioGroup(
        value="0",
        content=ft.Row([
            ft.Radio(value=str(index), label=label, active_color=theme.ACCENT,
                     label_style=ft.TextStyle(size=13, color=theme.TEXT))
            for index, (label, _w, _h) in enumerate(RESOLUTIONS)
        ], spacing=2),
    )

    legend_check = ft.Checkbox(label="Легенда на картинке", value=True,
                               active_color=theme.ACCENT,
                               label_style=ft.TextStyle(size=13, color=theme.TEXT))
    title_check = ft.Checkbox(label="Название созыва", value=False,
                              active_color=theme.ACCENT,
                              label_style=ft.TextStyle(size=13, color=theme.TEXT))

    preview_chart = SeatChart(total, rows,
                              [(color, seats) for _abbr, color, seats in distribution],
                              height=150)

    def confirm(_event) -> None:
        _label, width, height = RESOLUTIONS[int(resolution_picker.value)]
        on_confirm({
            "file_name": file_field.value,
            "width": width,
            "height": height,
            "with_legend": legend_check.value,
            "with_title": title_check.value,
        })

    return _shell(
        "Экспорт в PNG",
        [
            ft.Container(
                bgcolor=theme.BG,
                padding=12,
                content=ft.Column([
                    preview_chart,
                    ft.Row([
                        ft.Row([theme.swatch(color, 7),
                                ft.Text(f"{abbr} {seats}", size=10, color=theme.TEXT)],
                               spacing=5, tight=True)
                        for abbr, color, seats in distribution
                    ], wrap=True, spacing=14, run_spacing=4),
                ], spacing=4, tight=True),
            ),
            file_field,
            ft.Column([
                ft.Text("Разрешение", size=12, color=theme.NEUTRAL_700),
                resolution_picker,
            ], spacing=5, tight=True),
            ft.Row([legend_check, title_check], spacing=20),
        ],
        [_cancel(on_cancel), theme.primary_button("Сохранить как…", confirm)],
        width=500,
    )


# -- справка ----------------------------------------------------------------


def help_dialog(total: int, on_close: Callable) -> ft.AlertDialog:
    steps = [
        "Создайте партии в справочнике («Партии» в шапке): название, цвет, "
        "при желании — сокращение.",
        f"Распределите {total} мест в правой панели — схема пересчитывается сразу.",
        "Когда состав готов, нажмите «Новый созыв»: текущий уходит в историю, "
        "открывается следующий.",
        "Любой созыв из списка слева можно открыть и посмотреть; архивный "
        "правится по кнопке «Править состав».",
        "Кнопка «Экспортировать в PNG» сохраняет схему с легендой картинкой "
        "для чата или поста.",
    ]
    return _shell(
        "Как пользоваться",
        [
            ft.Column([
                ft.Row([
                    ft.Text(f"{index}.", size=14, width=20, color=theme.NEUTRAL_600),
                    ft.Container(ft.Text(step, size=14, color=theme.TEXT), expand=True),
                ], vertical_alignment=ft.CrossAxisAlignment.START)
                for index, step in enumerate(steps, 1)
            ], spacing=10, tight=True),
            ft.Text(
                "Проект сохраняется сам после каждого изменения. Через «Файл» можно "
                "завести отдельный файл под каждую игру.",
                size=13, color=theme.NEUTRAL_700,
            ),
        ],
        [theme.primary_button("Понятно", on_close)],
        width=520,
    )


# -- общий элемент ----------------------------------------------------------


def seat_bar(segments: list[tuple[str, int]], height: int = 5) -> ft.Control:
    """Полоска состава: сегмент на партию плюс серый остаток."""
    if not segments:
        return ft.Container(height=height, bgcolor=theme.NEUTRAL_300)
    return ft.Row(
        [ft.Container(bgcolor=color, expand=max(1, seats)) for color, seats in segments],
        spacing=1,
        height=height,
    )
