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
from . import format as fmt
from . import theme
from .mount import push
from .export import RESOLUTIONS, suggest_file_name
from .seat_chart import FILM_ALPHA, SeatChart, with_alpha

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


def coalition_dialog(coalition, parties: list, seats: dict, taken: dict,
                     used_colors: int,
                     on_save: Callable[[str, str, list], str | None],
                     on_cancel: Callable) -> ft.AlertDialog:
    """Сборка или правка блока: название, цвет плёнки и кто в него входит.

    :param taken: `{party_id: имя чужого блока}` — партии, уже занятые другой
                  коалицией. Их галочки заблокированы, а не просто ругаются
                  при сохранении: видно сразу, кто свободен.
    :param on_save: возвращает текст ошибки либо None, если сохранилось.

    Цвет здесь — не «цвет партии», а плотность плёнки над её местами, поэтому
    рядом с палитрой показан живой образец блока: полоски выбранных партий под
    выбранной краской. Так видно то же, что получится на схеме зала.
    """
    editing = coalition is not None
    start_color = (coalition.color if editing
                   else theme.PALETTE[used_colors % len(theme.PALETTE)])
    chosen: dict[str, bool] = {
        party.id: bool(editing and party.id in coalition.members)
        for party in parties
    }

    name_field = theme.text_field(coalition.name if editing else "",
                                  label_text="Название блока", autofocus=True)
    hex_field = theme.text_field(start_color.upper(), label_text="HEX", width=120,
                                 monospace=True)
    error = ft.Text("", size=theme.fs(12), color=theme.ACCENT_2_700, visible=False)
    preview = ft.Row(spacing=0, height=theme.fs(30))
    summary = ft.Text(size=theme.fs(12), color=theme.NEUTRAL_700)
    palette_row = ft.Row(spacing=_SWATCH_GAP, wrap=True, run_spacing=_SWATCH_GAP)

    state = {"color": start_color}

    def refresh_preview() -> None:
        inside = [p for p in parties if chosen.get(p.id)]
        total = sum(seats.get(p.id, 0) for p in inside)
        preview.controls = [
            ft.Container(expand=max(1, seats.get(party.id, 0)),
                         bgcolor=party.color, tooltip=party.name)
            for party in inside
        ] or [ft.Container(expand=True, bgcolor=theme.NEUTRAL_300)]
        # Плёнка — отдельный слой поверх полосок, ровно как на схеме зала.
        preview.controls = [ft.Stack([
            ft.Row(preview.controls, spacing=0, expand=True),
            ft.Container(expand=True, bgcolor=_film_color(state["color"])),
        ], expand=True)]
        summary.value = (
            f"{fmt.pluralize(len(inside), fmt.PARTIES_COUNT)} · "
            f"{fmt.pluralize(total, fmt.SEATS)}"
            if inside else "Партии не выбраны")
        push(preview)
        push(summary)

    def apply_color(color: str, update_hex: bool = True) -> None:
        state["color"] = color
        if update_hex:
            hex_field.value = color.upper()
        for control in palette_row.controls:
            if control.data:
                selected = control.data == color
                control.border = ft.Border.all(
                    2 if selected else 1,
                    theme.ACCENT if selected else "#1f000000")
        push(palette_row)
        push(hex_field)
        refresh_preview()

    def on_hex_change(_event) -> None:
        color = normalize_hex(hex_field.value)
        if color:
            apply_color(color, update_hex=False)

    hex_field.on_change = on_hex_change

    for color in theme.PALETTE:
        size = theme.fs(_SWATCH_SIZE)
        selected = color == start_color.lower()
        palette_row.controls.append(ft.Container(
            width=size, height=size, bgcolor=color, data=color,
            border_radius=theme.RADIUS,
            border=ft.Border.all(2 if selected else 1,
                                 theme.ACCENT if selected else "#1f000000"),
            on_click=lambda e: apply_color(e.control.data),
            ink=True,
        ))

    def toggle(party_id: str, value: bool) -> None:
        chosen[party_id] = value
        refresh_preview()

    rows: list[ft.Control] = []
    for party in parties:
        busy = taken.get(party.id)
        checkbox = ft.Checkbox(
            value=chosen[party.id],
            disabled=bool(busy),
            fill_color=theme.ACCENT,
            on_change=lambda e, pid=party.id: toggle(pid, bool(e.control.value)),
        )
        label = ft.Text(party.name, size=theme.fs(14),
                        color=theme.NEUTRAL_600 if busy else theme.TEXT,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        note = (f"в блоке «{busy}»" if busy
                else fmt.pluralize(seats.get(party.id, 0), fmt.SEATS))
        rows.append(ft.Row([
            checkbox,
            theme.swatch(party.color, 12),
            ft.Container(label, expand=True),
            ft.Text(note, size=theme.fs(12), color=theme.NEUTRAL_600),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def save(_event) -> None:
        members = [pid for pid, on in chosen.items() if on]
        message = on_save(name_field.value or "", state["color"], members)
        if message:
            _show_error(error, message)

    refresh_preview()
    return _shell(
        "Правка коалиции" if editing else "Собрать коалицию",
        [
            name_field,
            ft.Column([
                theme.label("Цвет плёнки"),
                ft.Row([hex_field], spacing=10),
                palette_row,
            ], spacing=8, tight=True),
            ft.Column([
                theme.label("Так блок ляжет на схему"),
                ft.Container(preview, border=ft.Border.all(1, theme.DIVIDER)),
                summary,
            ], spacing=6, tight=True),
            ft.Column([
                theme.label("Кто входит"),
                ft.Container(ft.Column(rows, spacing=2, scroll=ft.ScrollMode.AUTO),
                             height=min(220, 34 * max(1, len(rows)))),
            ], spacing=6, tight=True),
            error,
        ],
        [_cancel(on_cancel),
         theme.primary_button("Сохранить" if editing else "Собрать", save)],
        width=520,
    )


def _film_color(color: str) -> str:
    """Цвет плёнки с её прозрачностью — в том же виде, что и на схеме зала."""
    return with_alpha(color, FILM_ALPHA)


def delete_party_dialog(party: Party, usage: list[dict], plural: Callable,
                        on_confirm: Callable, on_cancel: Callable,
                        footprint: dict | None = None) -> ft.AlertDialog:
    """Подтверждение удаления партии — со всем, что уйдёт вместе с ней.

    `footprint` — сводка от `ParlamentService.party_footprint`: очки
    популярности копятся всю игру, и унести их молча было бы нечестно.
    """
    body: list[ft.Control] = []
    recounted = (footprint or {}).get("recountedConvocations", 0)
    if usage:
        body.append(ft.Text(
            "Округа этих созывов поделятся заново, уже без неё:" if recounted
            else "Места партии станут нераспределёнными:",
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

    points = (footprint or {}).get("supportPoints", 0)
    settlements = (footprint or {}).get("settlements", 0)
    if points:
        body.append(ft.Text(
            f"Пропадут {points} {plural(points, ['очко', 'очка', 'очков'])} "
            f"популярности в {settlements} "
            f"{plural(settlements, ['населённом пункте', 'населённых пунктах', 'населённых пунктах'])} "
            f"— их запас вернётся другим партиям.",
            size=theme.fs(13), color=theme.NEUTRAL_700))

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


def district_dialog(district, rows: list[tuple], shares: dict[str, float],
                    on_close: Callable, population: float = 0.0) -> ft.AlertDialog:
    """Расклад одного округа — по клику на карте.

    `rows` — отсортированные `(партия, PartyResult|None, места)`. Показываем
    слагаемые доли, а не только итог: колебание случайно и не повторится, и
    без разбивки потом не понять, почему округ достался этой партии.
    Партия ниже проходного барьера мест не получает, но её доля всё равно
    видна здесь — барьер запрещает места, а не участие.

    `population` — сколько человек в округе. Стоит рядом с мандатами не для
    красоты: с этим весом доля округа входит в общий процент голосов по
    стране, и по нему видно, почему четыре мандата Северного мыса весят в
    итогах меньше трёх мандатов Киркьюнивенского.
    """
    from ..elections import THRESHOLD_PERCENT

    head: list[ft.Control] = [
        ft.Text(f"{district.seats} мест", size=theme.fs(14),
                font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
    ]
    if population > 0:
        head.append(ft.Text(f"· {round(population / 1000):d} тыс. жителей",
                            size=theme.fs(13), color=theme.NEUTRAL_600))
    head.extend([
        ft.Container(expand=True),
        ft.Text(district.region, size=theme.fs(13), color=theme.NEUTRAL_600),
    ])
    body: list[ft.Control] = [ft.Row(head, spacing=8)]

    if not rows:
        body.append(ft.Text("По этому округу результатов ещё нет.",
                            size=theme.fs(14), color=theme.NEUTRAL_700))
        return _shell(district.name, body, [_cancel_as_close(on_close)])

    lines: list[ft.Control] = [
        ft.Row([
            ft.Container(theme.label("Партия"), expand=True),
            ft.Container(theme.label("Расчёт"), width=230),
            ft.Container(theme.label("% голосов"), width=90),
            ft.Container(theme.label("Мест"), width=48),
        ], spacing=8),
    ]
    for party, result, seats in rows:
        share = shares.get(party.id, 0.0)
        below_threshold = 0 < share < THRESHOLD_PERCENT
        share_text = f"{share:.1f} %".replace(".", ",")
        if below_threshold:
            share_text += " · ниже барьера"
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
                    ft.Text(_roll_breakdown(result), size=theme.fs(12),
                            color=theme.NEUTRAL_700),
                    width=230,
                ),
                ft.Container(
                    ft.Text(share_text, size=theme.fs(13),
                            color=theme.NEUTRAL_600 if below_threshold else theme.TEXT),
                    width=90,
                ),
                ft.Container(
                    ft.Text(str(seats), size=theme.fs(15),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                    width=48,
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ))
    body.extend(lines)
    return _shell(district.name, body, [_cancel_as_close(on_close)], width=660)


def _roll_breakdown(result) -> str:
    """«60,0 − 4,0 + 1,2 + 2,0 − 0,8 = 58,4» — из чего сложилась доля партии.

    Сумма — до нормировки на 100 % по округу, поэтому может слегка
    расходиться с «% голосов» в соседнем столбце: тот уже нормирован.
    """
    if result is None:
        return "—"
    parts = [f"{result.base:.1f}".replace(".", ",")]
    for value in (result.national, result.island, result.modifier):
        if value:
            sign = "+" if value > 0 else "−"
            parts.append(f"{sign} {abs(value):.1f}".replace(".", ","))
    if result.wobble:
        sign = "+" if result.wobble > 0 else "−"
        parts.append(f"{sign} {abs(result.wobble):.1f}".replace(".", ","))
    raw = result.raw
    total = f"{max(0.0, raw):.1f}".replace(".", ",")
    line = f"{' '.join(parts)} = {total} %"
    # Штрафы могли увести сумму в минус, а доля ниже нуля не бывает: иначе
    # партия вычитала бы голоса у остальных. Без оговорки строка выглядела
    # бы арифметической ошибкой.
    if raw < 0:
        line += " (не ниже нуля)"
    return line


def adopt_districts_dialog(old_total: int, districts: list, on_confirm: Callable,
                           on_cancel: Callable) -> ft.AlertDialog:
    """Предложение завести округа карты в проекте, начатом до её появления.

    Размер палаты при этом меняется, поэтому спрашиваем явно, а не делаем
    молча при открытии файла.
    """
    new_total = sum(d.seats for d in districts)
    body: list[ft.Control] = [
        ft.Text(f"В проект добавятся {len(districts)} округов игровой карты.",
                size=theme.fs(14), color=theme.TEXT),
        ft.Container(
            padding=ft.Padding.all(10),
            bgcolor=theme.NEUTRAL_100,
            border=ft.Border.all(1, theme.DIVIDER),
            border_radius=theme.RADIUS,
            content=ft.Column([
                ft.Row([
                    ft.Text("Мест в парламенте", size=theme.fs(13), color=theme.NEUTRAL_700),
                    ft.Container(expand=True),
                    ft.Text(f"{old_total} → {new_total}", size=theme.fs(14),
                            font_family=theme.FONT_SEMIBOLD, color=theme.TEXT),
                ]),
                ft.Text(
                    "Уже распределённые места сохранятся — просто часть палаты "
                    "станет нераспределённой, пока не пройдут выборы.",
                    size=theme.fs(12), color=theme.NEUTRAL_700,
                ),
            ], spacing=7, tight=True),
        ),
    ]
    return _shell("Взять округа с карты?", body,
                  [_cancel(on_cancel), theme.primary_button("Добавить округа", on_confirm)])


def settlement_dialog(district, on_confirm: Callable[[str], str | None],
                      on_cancel: Callable, settlement=None) -> ft.AlertDialog:
    """Новый населённый пункт в округе, а с `settlement` — переименование.

    Опечатка в названии не безобидна: таблица поддержки сопоставляет пункты
    по имени, и «Гавань» с «Гавнь» приехали бы в проект двумя разными.
    """
    current = settlement.name if settlement else ""
    name = theme.text_field(current, label_text="Название", autofocus=True)
    error = ft.Text("", size=theme.fs(12), color=theme.ACCENT_2_700, visible=False)

    def confirm(_event) -> None:
        message = on_confirm(name.value or "")
        if message:
            _show_error(error, message)

    name.on_submit = confirm
    return _shell(
        f"Населённый пункт — {district.name}",
        [name, error],
        [_cancel(on_cancel),
         theme.primary_button("Сохранить" if settlement else "Добавить", confirm)],
    )


def import_report_dialog(settlements: int, cities: int, warnings: list[str],
                         on_close: Callable) -> ft.AlertDialog:
    """Что удалось разобрать в загруженной таблице, а что вызвало вопросы.

    Показывается всегда, даже без единого замечания: тост исчезает с экрана
    сам, раньше, чем его успевают прочитать, а тут решает сам человек, когда
    закрыть — и видит не общее число строк, а что именно из них — сёла,
    что — города, чтобы можно было сверить с документом на глаз.

    Замечания показываются списком, а не одной строкой: по ним пользователь
    правит свой документ, и обрезать их было бы вредно.
    """
    filled = settlements + cities
    if filled:
        parts = []
        if settlements:
            parts.append(fmt.pluralize(settlements, fmt.SETTLEMENTS))
        if cities:
            parts.append(fmt.pluralize(cities, fmt.CITIES))
        summary = f"Занесено очков поддержки: {', '.join(parts)}."
    else:
        summary = "Ни одной строки разобрать не удалось."

    body: list[ft.Control] = [
        ft.Text(summary, size=theme.fs(14), color=theme.TEXT),
    ]
    if warnings:
        body.append(ft.Container(
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
        ))
    title = "Таблица загружена с замечаниями" if warnings else "Таблица загружена"
    return _shell(title, body, [_cancel_as_close(on_close)], width=520)


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
                  on_cancel: Callable,
                  chart_dist: list[tuple] | None = None) -> ft.AlertDialog:
    """`distribution` — тройки «сокращение, цвет, мест» для предпросмотра.

    `chart_dist` — то же самое в виде, который понимает схема, но с плёнками
    коалиций: без него предпросмотр показал бы зал без блоков, а картинка на
    выходе — с ними.
    """
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

    preview_chart = SeatChart(
        total, rows,
        chart_dist if chart_dist is not None
        else [(color, seats) for _abbr, color, seats in distribution],
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


def map_export_dialog(convocation_name: str, districts,
                      legend: list[tuple[str, str, int, int]],
                      background,
                      on_confirm: Callable[[dict], None],
                      on_cancel: Callable) -> ft.AlertDialog:
    """Тот же диалог экспорта, что и у схемы зала, но для карты округов.

    `districts` — `(код, название, мест, цвет|None)` для предпросмотра;
    `legend` — `(название партии, цвет, округов, мест)` строками сводки.
    """
    from .map_chart import MapChart

    file_field = theme.text_field(suggest_file_name(convocation_name, prefix="Карта"),
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
    title_check = ft.Checkbox(label="Название созыва", value=True,
                              active_color=theme.ACCENT,
                              label_style=ft.TextStyle(size=theme.fs(13), color=theme.TEXT))

    preview_map = MapChart(districts=districts, background=background, height=150)

    def confirm(_event) -> None:
        _label, width, _height = RESOLUTIONS[int(resolution_picker.value)]
        on_confirm({
            "file_name": file_field.value,
            "width": width,
            "with_legend": legend_check.value,
            "with_title": title_check.value,
        })

    return _shell(
        "Экспорт карты в PNG",
        [
            ft.Container(
                bgcolor=theme.BG,
                padding=12,
                content=ft.Column([
                    preview_map,
                    ft.Row([
                        ft.Row([theme.swatch(color, 7),
                                ft.Text(f"{name} {seats}", size=theme.fs(10), color=theme.TEXT)],
                               spacing=5, tight=True)
                        for name, color, _won, seats in legend
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
