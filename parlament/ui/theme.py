"""Дизайн-система Broadsheet: цвета, размеры и шрифт из макетов.

Значения перенесены из `_ds/broadsheet/styles.css` дизайн-передачи. Держим их
в одном месте, чтобы экраны не расходились с макетом и между собой.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft

# -- цвета ------------------------------------------------------------------

BG = "#f3f2f2"            # бумага, фон приложения
SURFACE = "#eae9e9"       # фон полей ввода и диалогов
TEXT = "#201e1d"          # основная краска
DIVIDER = "#d0cecd"       # разделители (тот же тон, что color-mix 16 % в CSS)

ACCENT = "#0088b0"        # голубой: интерактивные элементы
ACCENT_100 = "#e9f8ff"    # подсветка выбранного созыва
ACCENT_200 = "#cbeeff"
ACCENT_600 = "#1186ac"
ACCENT_700 = "#006786"    # голубой, безопасный для текста

ACCENT_2 = "#d6006c"      # пурпурный: предупреждения и опасные действия
ACCENT_2_600 = "#d82071"
ACCENT_2_700 = "#aa0b56"  # пурпурный, безопасный для текста

NEUTRAL_100 = "#f8f4f4"
NEUTRAL_200 = "#eae7e7"
NEUTRAL_300 = "#d7d3d3"   # нераспределённые места и фон полос
NEUTRAL_600 = "#7d7979"   # подписи и второстепенный текст
NEUTRAL_700 = "#605d5d"   # третичный текст
NEUTRAL_900 = "#2d2b2b"

EMPTY_SEAT = NEUTRAL_300

#: Готовая палитра для новых партий: восемь фирменных цветов системы
#: (акценты и цвета партий из макетов) плюс дополненные ими промежуточные
#: оттенки — так в палитре редко приходится доходить до своего цвета.
PALETTE = [
    "#0088b0", "#d6006c", "#4c7a34", "#edbb00",
    "#2d2b2b", "#b3541e", "#7d7979", "#5b4b8a",
]


def _generate_extra_swatches(count: int) -> list[str]:
    """Довешивает к фирменным цветам равномерно разнесённые по тону оттенки —
    та же насыщенность/светлота, что и у остальной палитры, так что новые
    цвета не выбиваются из неё."""
    import colorsys

    used_hues = sorted(colorsys.rgb_to_hls(
        int(c[1:3], 16) / 255, int(c[3:5], 16) / 255, int(c[5:7], 16) / 255,
    )[0] for c in PALETTE)

    extra = []
    for i in range(count):
        # Каждый новый оттенок — подальше от уже занятых (золотое сечение
        # уводит серию от повторов лучше, чем равный шаг).
        hue = (used_hues[0] + (i + 1) * 0.618_034) % 1.0
        r, g, b = colorsys.hls_to_rgb(hue, 0.42, 0.55)
        extra.append("#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255)))
    return extra


PALETTE = PALETTE + _generate_extra_swatches(8)

# -- шрифт ------------------------------------------------------------------

FONT_FAMILY = "Source Serif 4"
FONT_SEMIBOLD = "Source Serif 4 SemiBold"

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

#: Шрифты лежат в репозитории — приложению не нужен интернет.
FONTS = {
    FONT_FAMILY: "fonts/SourceSerif4-Regular.ttf",
    FONT_SEMIBOLD: "fonts/SourceSerif4-SemiBold.ttf",
}

# -- размеры ----------------------------------------------------------------

RAIL_LEFT_WIDTH = 214
RAIL_RIGHT_WIDTH = 330
RADIUS = 2                # система принципиально почти без скруглений

WINDOW_WIDTH = 1300
WINDOW_HEIGHT = 820
WINDOW_MIN_WIDTH = 1080
WINDOW_MIN_HEIGHT = 700

#: Макет верстался как веб-страница; тот же px на десктопном окне читается
#: мелко — все текстовые размеры проходят через этот множитель.
FONT_SCALE = 1.2


def fs(px: float) -> int:
    """Масштабированный размер текста (и парных с ним элементов — маркеров,
    квадратиков-образцов цвета)."""
    return round(px * FONT_SCALE)


def build_theme() -> ft.Theme:
    """Тема Flet, приведённая к палитре Broadsheet."""
    return ft.Theme(
        font_family=FONT_FAMILY,
        color_scheme=ft.ColorScheme(
            primary=ACCENT,
            on_primary=BG,
            surface=BG,
            on_surface=TEXT,
            surface_tint=ft.Colors.TRANSPARENT,
            outline=DIVIDER,
            error=ACCENT_2_700,
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color=NEUTRAL_300,
            thickness=8,
            radius=0,
        ),
    )


# -- типовые элементы -------------------------------------------------------


def label(text: str) -> ft.Text:
    """Заголовок панели: «СОЗЫВЫ», «РАСПРЕДЕЛЕНИЕ МЕСТ»."""
    return ft.Text(
        text.upper(),
        size=fs(11),
        color=NEUTRAL_600,
        weight=ft.FontWeight.W_400,
        # letter-spacing .1em из макета
        style=ft.TextStyle(letter_spacing=1.1),
    )


def heading(text: str, size: int = 26) -> ft.Text:
    return ft.Text(text, size=fs(size), font_family=FONT_SEMIBOLD, color=TEXT, no_wrap=True)


def swatch(color: str, size: int = 12) -> ft.Container:
    """Квадратик цвета партии — легенда, списки, таблица."""
    scaled = fs(size)
    return ft.Container(
        width=scaled,
        height=scaled,
        bgcolor=color,
        border=ft.Border.all(1, "#1f000000"),
    )


def _button_style(bgcolor: str, color: str, hover: str,
                  border: str | None = None,
                  padding_h: int = 18) -> ft.ButtonStyle:
    """Общий каркас стиля кнопки.

    Цвета задаются простыми значениями, а не картой состояний: карта без ключа
    под текущее состояние заставляет Flet взять цвет из темы, и кнопка получает
    чужую заливку. Наведение и нажатие идут через `overlay_color`.
    """
    return ft.ButtonStyle(
        bgcolor=bgcolor,
        color=color,
        overlay_color=hover,
        side=ft.BorderSide(1, border) if border else None,
        shape=ft.RoundedRectangleBorder(radius=RADIUS),
        padding=ft.Padding.symmetric(horizontal=padding_h, vertical=15),
        text_style=ft.TextStyle(size=fs(14), font_family=FONT_SEMIBOLD),
        elevation=0,
        shadow_color=ft.Colors.TRANSPARENT,
    )


def primary_button(text: str, on_click, disabled: bool = False, danger: bool = False,
                   expand: bool = False, tooltip: str | None = None) -> ft.Button:
    """Основное действие — сплошная заливка акцентом (или пурпуром, если опасное)."""
    base = ACCENT_2_700 if danger else ACCENT
    hover = ACCENT_2_600 if danger else ACCENT_600
    return ft.Button(
        text,
        on_click=on_click,
        disabled=disabled,
        expand=expand,
        tooltip=tooltip,
        style=_button_style(base, BG, hover),
    )


def secondary_button(text: str, on_click, disabled: bool = False,
                     expand: bool = False, tooltip: str | None = None) -> ft.Button:
    """Второстепенное действие — тонкая рамка, без заливки."""
    return ft.Button(
        text,
        on_click=on_click,
        disabled=disabled,
        expand=expand,
        tooltip=tooltip,
        style=_button_style(ft.Colors.TRANSPARENT, TEXT, "#14201e1d", border=DIVIDER),
    )


def ghost_button(text: str, on_click, danger: bool = False,
                 disabled: bool = False, tooltip: str | None = None) -> ft.Button:
    """Текстовое действие без рамки — «Переименовать», «Изменить», «Удалить»."""
    tone = ACCENT_2_700 if danger else ACCENT
    return ft.Button(
        text,
        on_click=on_click,
        disabled=disabled,
        tooltip=tooltip,
        style=_button_style(ft.Colors.TRANSPARENT, tone,
                            f"1a{tone.lstrip('#')}", padding_h=8),
    )


def text_field(value: str = "", width: int | None = None, hint: str = "",
               on_change=None, on_submit=None, text_align=None,
               monospace: bool = False, autofocus: bool = False,
               keyboard_numeric: bool = False, label_text: str | None = None) -> ft.TextField:
    """Поле ввода в оформлении системы: заливка surface, рамка-волосок."""
    return ft.TextField(
        value=value,
        width=width,
        hint_text=hint,
        label=label_text,
        autofocus=autofocus,
        on_change=on_change,
        on_submit=on_submit,
        text_align=text_align or ft.TextAlign.LEFT,
        keyboard_type=ft.KeyboardType.NUMBER if keyboard_numeric else None,
        text_style=ft.TextStyle(
            size=fs(14),
            font_family="monospace" if monospace else FONT_FAMILY,
            color=TEXT,
        ),
        label_style=ft.TextStyle(size=fs(12), color=NEUTRAL_700),
        hint_style=ft.TextStyle(size=fs(14), color=NEUTRAL_600),
        bgcolor=SURFACE,
        filled=True,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=10, vertical=13),
        border_radius=RADIUS,
        border_color=DIVIDER,
        focused_border_color=ACCENT,
        cursor_color=ACCENT,
    )
