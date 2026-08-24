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


def party_dialog(page: ft.Page, party: Party | None, used_colors: int,
                 on_save: Callable[[str, str], str | None],
                 on_cancel: Callable,
                 recent_colors: list[str] | None = None,
                 on_custom_color_picked: Callable[[str], None] | None = None) -> ft.AlertDialog:
    """Создание или правка партии: название и цвет.

    `page` нужен, чтобы кнопка «Свой цвет» могла открыть подбор поверх этого
    диалога — через `page.show_dialog`, а не через `control.page` первого
    попавшегося поля: до показа диалог ещё не привязан к странице, и такое
    обращение упало бы.
    `on_save` возвращает текст ошибки либо None, если всё сохранилось.
    `recent_colors` — свои цвета, подобранные в предыдущих диалогах за эту
    сессию (свежий слева); показываются отдельной строкой под палитрой, чтобы
    не открывать подбор заново для похожих партий подряд. `on_custom_color_picked`
    зовётся при подтверждении в подборе цвета — так вызывающая сторона узнаёт,
    что цвет стоит запомнить.
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
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
    )
    error = ft.Text("", size=theme.fs(12), color=theme.ACCENT_2_700, visible=False)
    palette_row = ft.Row(spacing=_SWATCH_GAP, wrap=True, run_spacing=_SWATCH_GAP)
    recent_row = ft.Row(spacing=_SWATCH_GAP, wrap=True, run_spacing=_SWATCH_GAP)
    swatch_rows = [palette_row, recent_row]

    state = {"color": start_color}

    def apply_color(color: str, update_hex: bool = True) -> None:
        state["color"] = color
        if update_hex:
            hex_field.value = color.upper()
        swatch_preview.bgcolor = color
        for row in swatch_rows:
            for control in row.controls:
                if control.data:
                    selected = control.data == color
                    control.border = ft.Border.all(
                        2 if selected else 1,
                        theme.ACCENT if selected else "#1f000000",
                    )
        for control in (*swatch_rows, hex_field, swatch_preview):
            push(control)

    def pick_custom_color(color: str) -> None:
        apply_color(color)
        if on_custom_color_picked:
            on_custom_color_picked(color)

    def on_hex_change(_event) -> None:
        color = normalize_hex(hex_field.value)
        if color:
            apply_color(color, update_hex=False)

    hex_field.on_change = on_hex_change

    def make_swatch(color: str) -> ft.Container:
        swatch_size = theme.fs(_SWATCH_SIZE)
        selected = color == start_color.lower()
        return ft.Container(
            width=swatch_size, height=swatch_size, bgcolor=color, data=color,
            border_radius=theme.RADIUS,
            border=ft.Border.all(2 if selected else 1,
                                 theme.ACCENT if selected else "#1f000000"),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            tooltip=color,
            ink=True,
            on_click=lambda e: apply_color(e.control.data),
        )

    for color in theme.PALETTE:
        palette_row.controls.append(make_swatch(color))
    for color in recent_colors or []:
        recent_row.controls.append(make_swatch(color))

    swatch_size = theme.fs(_SWATCH_SIZE)
    palette_row.controls.append(
        ft.Container(
            width=swatch_size, height=swatch_size, data=None,
            bgcolor=theme.SURFACE, border_radius=theme.RADIUS,
            border=ft.Border.all(1, theme.DIVIDER),
            tooltip="Свой цвет",
            ink=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.ADD_ROUNDED, size=theme.fs(16), color=theme.NEUTRAL_700),
            on_click=lambda _e: _open_custom_color_dialog(page, state["color"], pick_custom_color),
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

    body = [
        name_field,
        ft.Row([swatch_preview, hex_field],
              spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
        palette_row,
    ]
    if recent_row.controls:
        body.append(ft.Container(height=1, bgcolor=theme.DIVIDER))
        body.append(recent_row)
    body.append(error)

    return _shell(
        "Изменить партию" if editing else "Новая партия",
        body,
        [_cancel(on_cancel), theme.primary_button("Сохранить", save)],
    )


def _show_error(control: ft.Text, message: str) -> None:
    control.value = message
    control.visible = True
    push(control)


#: Размеры квадрата насыщенность/яркость и полосы тона.
_SV_SIZE = (280, 160)
_HUE_SIZE = (280, 22)
_SV_HANDLE = 16
_HUE_HANDLE = 18


def _open_custom_color_dialog(page: ft.Page, current: str,
                              on_pick: Callable[[str], None]) -> None:
    """Подбор произвольного цвета — квадрат «насыщенность × яркость» плюс
    полоса тона, перетаскиваемые мышью.

    Ближе всего к тому, что раньше открывал системный `<input type="color">`
    браузера: одна цветовая плоскость, по которой водишь курсором, а не набор
    линейных ползунков. Настоящий системный диалог выбора цвета Flet не
    поддерживает (такого API у него нет), поэтому здесь та же идея — но
    нарисованная своими средствами: SV-квадрат собран из двух наложенных
    градиентов (белый→тон по горизонтали, прозрачный→чёрный по вертикали —
    стандартный приём, не требующий рисования по пикселям), а полоса тона —
    один семиточечный радужный градиент.
    """
    import colorsys

    sq_w, sq_h = _SV_SIZE
    hue_w, hue_h = _HUE_SIZE

    r, g, b = (int(current[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    state = {"h": hue, "s": sat, "v": val}

    def hue_hex(h: float) -> str:
        red, green, blue = colorsys.hsv_to_rgb(h, 1, 1)
        return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))

    def current_hex() -> str:
        red, green, blue = colorsys.hsv_to_rgb(state["h"], state["s"], state["v"])
        return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))

    preview = ft.Container(width=56, height=40, bgcolor=current, border_radius=theme.RADIUS,
                           border=ft.Border.all(1, "#1f000000"))
    value_text = ft.Text(current.upper(), size=theme.fs(14), font_family="monospace",
                         color=theme.TEXT)

    # -- SV-квадрат: тон по горизонтали (белый → чистый тон), яркость по
    #    вертикали (прозрачный → чёрный), сверху — кружок-курсор.
    sv_tint = ft.Container(width=sq_w, height=sq_h, gradient=ft.LinearGradient(
        begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0),
        colors=["#ffffff", hue_hex(state["h"])],
    ))
    sv_shade = ft.Container(width=sq_w, height=sq_h, gradient=ft.LinearGradient(
        begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
        colors=["#00000000", "#ff000000"],
    ))
    sv_cursor = ft.Container(
        width=_SV_HANDLE, height=_SV_HANDLE, border_radius=_SV_HANDLE / 2,
        left=state["s"] * sq_w - _SV_HANDLE / 2, top=(1 - state["v"]) * sq_h - _SV_HANDLE / 2,
        border=ft.Border.all(2, "#ffffff"),
        shadow=ft.BoxShadow(blur_radius=3, color="#80000000"),
    )
    sv_area = ft.Container(
        content=ft.Stack([sv_tint, sv_shade, sv_cursor], width=sq_w, height=sq_h),
        width=sq_w, height=sq_h, border_radius=theme.RADIUS, clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    # -- полоса тона: радужный градиент через шесть базовых цветов по кругу.
    hue_bar = ft.Container(width=hue_w, height=hue_h, border_radius=theme.RADIUS, gradient=ft.LinearGradient(
        begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0),
        colors=["#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"],
    ))
    hue_cursor = ft.Container(
        width=_HUE_HANDLE, height=_HUE_HANDLE, border_radius=_HUE_HANDLE / 2,
        left=state["h"] * hue_w - _HUE_HANDLE / 2, top=hue_h / 2 - _HUE_HANDLE / 2,
        bgcolor=hue_hex(state["h"]),
        border=ft.Border.all(2, "#ffffff"),
        shadow=ft.BoxShadow(blur_radius=3, color="#80000000"),
    )
    hue_area = ft.Container(
        content=ft.Stack([hue_bar, hue_cursor], width=hue_w, height=hue_h + _HUE_HANDLE),
        width=hue_w, height=hue_h + _HUE_HANDLE,
    )

    def refresh_preview() -> None:
        color = current_hex()
        preview.bgcolor = color
        value_text.value = color.upper()
        push(preview)
        push(value_text)

    def move_sv(x: float, y: float) -> None:
        x = min(max(x, 0), sq_w)
        y = min(max(y, 0), sq_h)
        state["s"] = x / sq_w
        state["v"] = 1 - y / sq_h
        sv_cursor.left = x - _SV_HANDLE / 2
        sv_cursor.top = y - _SV_HANDLE / 2
        push(sv_cursor)
        refresh_preview()

    def move_hue(x: float) -> None:
        x = min(max(x, 0), hue_w)
        state["h"] = x / hue_w
        color = hue_hex(state["h"])
        hue_cursor.left = x - _HUE_HANDLE / 2
        hue_cursor.bgcolor = color
        sv_tint.gradient = ft.LinearGradient(
            begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0), colors=["#ffffff", color])
        push(hue_cursor)
        push(sv_tint)
        refresh_preview()

    def on_sv_point(e) -> None:
        move_sv(e.local_position.x, e.local_position.y)

    def on_hue_point(e) -> None:
        move_hue(e.local_position.x)

    sv_gesture = ft.GestureDetector(
        content=sv_area, drag_interval=16,
        on_tap_down=on_sv_point, on_pan_start=on_sv_point, on_pan_update=on_sv_point,
    )
    hue_gesture = ft.GestureDetector(
        content=hue_area, drag_interval=16,
        on_tap_down=on_hue_point, on_pan_start=on_hue_point, on_pan_update=on_hue_point,
    )

    def close(_event) -> None:
        page.pop_dialog()

    def confirm(_event) -> None:
        on_pick(current_hex())
        page.pop_dialog()

    page.show_dialog(_shell(
        "Свой цвет",
        [
            sv_gesture,
            hue_gesture,
            ft.Row([preview, value_text], spacing=12,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        [_cancel(close), theme.primary_button("Выбрать", confirm)],
        width=sq_w + 40,
    ))


# -- удаление партии --------------------------------------------------------


def delete_party_dialog(party: Party, usage: list[dict], plural: Callable,
                        on_confirm: Callable, on_cancel: Callable) -> ft.AlertDialog:
    body: list[ft.Control] = []
    if usage:
        body.append(ft.Text("Места партии станут нераспределёнными:",
                            size=theme.fs(14), color=theme.TEXT))
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


# -- удаление созыва ---------------------------------------------------------


def delete_convocation_dialog(convocation: Convocation, on_confirm: Callable,
                              on_cancel: Callable) -> ft.AlertDialog:
    return _shell(
        f"Удалить «{convocation.name}»?",
        [ft.Text("Состав этого созыва безвозвратно удалится из истории.",
                 size=theme.fs(14), color=theme.TEXT)],
        [_cancel(on_cancel),
         theme.primary_button("Удалить всё равно", on_confirm, danger=True)],
    )


# -- округ и выборы ---------------------------------------------------------


def district_dialog(district, rows: list[tuple], total_votes: int,
                    on_close: Callable) -> ft.AlertDialog:
    """Расклад одного округа — по клику на маркер карты.

    `rows` — уже отсортированные `(партия, голоса, места)`.
    """
    body: list[ft.Control] = [
        ft.Row([
            ft.Text(f"{district.seats} мест", size=theme.fs(14),
                    font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
            ft.Container(expand=True),
            ft.Text(district.region, size=theme.fs(13), color=theme.NEUTRAL_600),
        ]),
    ]

    if not rows:
        body.append(ft.Text("По этому округу результатов ещё нет.",
                            size=theme.fs(14), color=theme.NEUTRAL_700))
        return _shell(district.name, body, [_cancel_as_close(on_close)])

    lines: list[ft.Control] = [
        ft.Row([
            ft.Container(theme.label("Партия"), expand=True),
            ft.Container(theme.label("Голоса"), width=90),
            ft.Container(theme.label("Мест"), width=54),
        ], spacing=8),
    ]
    for party, votes, seats in rows:
        share = f"{votes / total_votes * 100:.1f} %".replace(".", ",") if total_votes else "—"
        lines.append(ft.Container(
            padding=ft.Padding.symmetric(vertical=6),
            border=ft.Border.only(bottom=ft.BorderSide(1, "#14201e1d")),
            content=ft.Row([
                theme.swatch(party.color, 11),
                ft.Container(
                    ft.Text(party.name, size=theme.fs(14), color=theme.TEXT, no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS, tooltip=party.name),
                    expand=True,
                ),
                ft.Container(
                    ft.Column([
                        ft.Text(f"{votes}", size=theme.fs(13), color=theme.TEXT),
                        ft.Text(share, size=theme.fs(11), color=theme.NEUTRAL_600),
                    ], spacing=0, tight=True),
                    width=90,
                ),
                ft.Container(
                    ft.Text(str(seats), size=theme.fs(15),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                    width=54,
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ))
    body.extend(lines)
    return _shell(district.name, body, [_cancel_as_close(on_close)], width=520)


def import_report_dialog(filled: int, warnings: list[str],
                         on_close: Callable) -> ft.AlertDialog:
    """Что удалось разобрать в загруженной таблице, а что вызвало вопросы.

    Замечания показываются списком, а не одной строкой: по ним пользователь
    правит свой документ, и обрезать их было бы вредно.
    """
    body: list[ft.Control] = [
        ft.Text(
            f"Загружено округов: {filled}." if filled
            else "Ни одной строки с результатами разобрать не удалось.",
            size=theme.fs(14), color=theme.TEXT,
        ),
        ft.Container(
            padding=ft.Padding.all(10),
            bgcolor=theme.NEUTRAL_100,
            border=ft.Border.all(1, theme.DIVIDER),
            border_radius=theme.RADIUS,
            content=ft.Column(
                [ft.Text(f"• {text}", size=theme.fs(12), color=theme.NEUTRAL_700)
                 for text in warnings],
                spacing=5, tight=True, scroll=ft.ScrollMode.AUTO,
            ),
            height=min(240, 30 + 22 * len(warnings)),
        ),
    ]
    return _shell("Таблица загружена с замечаниями", body,
                  [_cancel_as_close(on_close)], width=520)


def _cancel_as_close(on_close: Callable) -> ft.Control:
    return theme.primary_button("Закрыть", on_close)


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
        [field],
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
