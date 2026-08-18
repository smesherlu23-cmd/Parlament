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
        title=ft.Text(title, size=theme.fs(20), font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
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


_SWATCH_SIZE = 34
_SWATCH_GAP = 8


def party_dialog(party: Party | None, used_colors: int,
                 on_save: Callable[[str, str], str | None],
                 on_cancel: Callable) -> ft.AlertDialog:
    """Создание или правка партии: название и цвет.

    `on_save` возвращает текст ошибки либо None, если всё сохранилось.
    """
    editing = party is not None
    start_color = party.color if party else theme.PALETTE[used_colors % len(theme.PALETTE)]

    name_field = theme.text_field(party.name if party else "", label_text="Название",
                                  autofocus=True)
    hex_field = theme.text_field(start_color.upper(), label_text="HEX", width=120,
                                 monospace=True)
    swatch_preview = ft.Container(
        width=theme.fs(_SWATCH_SIZE), height=theme.fs(_SWATCH_SIZE),
        bgcolor=start_color, border_radius=theme.RADIUS,
        border=ft.Border.all(1, "#1f000000"),
    )
    error = ft.Text("", size=theme.fs(12), color=theme.ACCENT_2_700, visible=False)
    palette_row = ft.Row(spacing=_SWATCH_GAP, wrap=True, run_spacing=_SWATCH_GAP)

    state = {"color": start_color}

    def apply_color(color: str, update_hex: bool = True) -> None:
        state["color"] = color
        if update_hex:
            hex_field.value = color.upper()
        swatch_preview.bgcolor = color
        for control in palette_row.controls:
            if control.data:
                selected = control.data == color
                control.border = ft.Border.all(
                    2 if selected else 1,
                    theme.ACCENT if selected else "#1f000000",
                )
        for control in (palette_row, hex_field, swatch_preview):
            push(control)

    def on_hex_change(_event) -> None:
        color = normalize_hex(hex_field.value)
        if color:
            apply_color(color, update_hex=False)

    hex_field.on_change = on_hex_change

    swatch_size = theme.fs(_SWATCH_SIZE)
    for color in theme.PALETTE:
        selected = color == start_color.lower()
        palette_row.controls.append(
            ft.Container(
                width=swatch_size, height=swatch_size, bgcolor=color, data=color,
                border_radius=theme.RADIUS,
                border=ft.Border.all(2 if selected else 1,
                                     theme.ACCENT if selected else "#1f000000"),
                tooltip=color,
                ink=True,
                on_click=lambda e: apply_color(e.control.data),
            )
        )
    palette_row.controls.append(
        ft.Container(
            width=swatch_size, height=swatch_size, data=None,
            bgcolor=theme.SURFACE, border_radius=theme.RADIUS,
            border=ft.Border.all(1, theme.DIVIDER),
            tooltip="Свой цвет",
            ink=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.ADD_ROUNDED, size=theme.fs(16), color=theme.NEUTRAL_700),
            on_click=lambda _e: _open_custom_color_dialog(name_field.page, state["color"], apply_color),
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
        message = on_save(name_field.value, color)
        if message:
            _show_error(error, message)

    return _shell(
        "Изменить партию" if editing else "Новая партия",
        [
            name_field,
            ft.Row([swatch_preview, hex_field],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
            palette_row,
            error,
        ],
        [_cancel(on_cancel), theme.primary_button("Сохранить", save)],
    )


def _show_error(control: ft.Text, message: str) -> None:
    control.value = message
    control.visible = True
    push(control)


def _open_custom_color_dialog(page: ft.Page, current: str,
                              on_pick: Callable[[str], None]) -> None:
    """Подбор произвольного цвета через тон/насыщенность/светлоту.

    Три канала RGB неудобны для подбора «на глаз» — сдвиг одного канала
    меняет и яркость, и оттенок сразу. HSL разводит эти вещи по разным
    ползункам: тон выбирает цвет, насыщенность и светлота — его вид.
    """
    import colorsys

    r, g, b = (int(current[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)

    preview = ft.Container(width=56, height=40, bgcolor=current, border_radius=theme.RADIUS,
                           border=ft.Border.all(1, "#1f000000"))
    value_text = ft.Text(current.upper(), size=theme.fs(14), font_family="monospace",
                         color=theme.TEXT)

    def current_hex() -> str:
        red, green, blue = colorsys.hls_to_rgb(
            hue_slider.value / 360, light_slider.value / 100, sat_slider.value / 100)
        return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))

    def on_slide(_event) -> None:
        color = current_hex()
        preview.bgcolor = color
        value_text.value = color.upper()
        for label, slider, suffix in (
            (hue_label, hue_slider, "°"), (sat_label, sat_slider, " %"), (light_label, light_slider, " %"),
        ):
            label.value = f"{label.data} {round(slider.value)}{suffix}"
            push(label)
        push(preview)
        push(value_text)

    hue_slider = ft.Slider(min=0, max=360, value=hue * 360,
                           active_color=theme.ACCENT, on_change=on_slide)
    sat_slider = ft.Slider(min=0, max=100, value=sat * 100,
                           active_color=theme.ACCENT, on_change=on_slide)
    light_slider = ft.Slider(min=0, max=100, value=light * 100,
                             active_color=theme.ACCENT, on_change=on_slide)

    def value_label(text: str, slider: ft.Slider, suffix: str) -> ft.Text:
        label = ft.Text(f"{text} {round(slider.value)}{suffix}", size=theme.fs(12),
                        color=theme.NEUTRAL_700)
        label.data = text
        return label

    hue_label = value_label("Тон", hue_slider, "°")
    sat_label = value_label("Насыщенность", sat_slider, " %")
    light_label = value_label("Светлота", light_slider, " %")

    def close(_event) -> None:
        page.pop_dialog()

    def confirm(_event) -> None:
        on_pick(current_hex())
        page.pop_dialog()

    def slider_row(label: ft.Text, slider: ft.Slider) -> ft.Control:
        return ft.Column([label, slider], spacing=0, tight=True)

    page.show_dialog(_shell(
        "Свой цвет",
        [
            ft.Row([preview, value_text], spacing=12,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER),
            slider_row(hue_label, hue_slider),
            slider_row(sat_label, sat_slider),
            slider_row(light_label, light_slider),
        ],
        [_cancel(close), theme.primary_button("Выбрать", confirm)],
        width=380,
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

    body: list[ft.Control] = [ft.Text(explanation, size=theme.fs(14), color=theme.TEXT)]
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
                        size=theme.fs(13), color=theme.TEXT,
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
                size=theme.fs(14), color=theme.TEXT,
            ),
            ft.Container(
                bgcolor=theme.BG,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Column([
                    seat_bar(segments, height=8),
                    ft.Text(
                        f"{used} из {total} мест распределены · "
                        f"{party_count} {plural(party_count, ['партия', 'партии', 'партий'])}",
                        size=theme.fs(12), color=theme.NEUTRAL_700,
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
                    size=theme.fs(14), color=theme.NEUTRAL_700),
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
                     label_style=ft.TextStyle(size=theme.fs(13), color=theme.TEXT))
            for index, (label, _w, _h) in enumerate(RESOLUTIONS)
        ], spacing=2),
    )

    legend_check = ft.Checkbox(label="Легенда на картинке", value=True,
                               active_color=theme.ACCENT,
                               label_style=ft.TextStyle(size=theme.fs(13), color=theme.TEXT))
    title_check = ft.Checkbox(label="Название созыва", value=False,
                              active_color=theme.ACCENT,
                              label_style=ft.TextStyle(size=theme.fs(13), color=theme.TEXT))

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
                                ft.Text(f"{abbr} {seats}", size=theme.fs(10), color=theme.TEXT)],
                               spacing=5, tight=True)
                        for abbr, color, seats in distribution
                    ], wrap=True, spacing=14, run_spacing=4),
                ], spacing=4, tight=True),
            ),
            file_field,
            ft.Column([
                ft.Text("Разрешение", size=theme.fs(12), color=theme.NEUTRAL_700),
                resolution_picker,
            ], spacing=5, tight=True),
            ft.Row([legend_check, title_check], spacing=20),
        ],
        [_cancel(on_cancel), theme.primary_button("Сохранить как…", confirm)],
        width=500,
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
