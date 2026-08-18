"""Безопасное обновление контролов.

`Control.page` у непривязанного контрола не возвращает None, а бросает
RuntimeError, поэтому «обновить, если уже на экране» приходится выражать
явным обходом цепочки родителей. Это нужно и при сборке экрана (контролы
ещё не привязаны), и в тестах (страница подставная).
"""

from __future__ import annotations

import flet as ft


def is_mounted(control: ft.Control | None) -> bool:
    """Привязан ли контрол к настоящей странице Flet."""
    node = control
    while node is not None:
        if isinstance(node, ft.Page):
            return True
        node = getattr(node, "parent", None)
    return False


def push(control: ft.Control | None) -> None:
    """Отправляет контрол на экран, если он уже привязан к странице."""
    if is_mounted(control):
        control.update()
