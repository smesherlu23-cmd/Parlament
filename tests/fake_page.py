"""Подставная страница Flet для тестов.

Настоящая `ft.Page` требует запущенного клиента Flutter, поэтому тесты
интерфейса работают с заглушкой: она принимает те же обращения (`add`,
`update`, `show_dialog`, `run_task`, `window`, `services`) и запоминает, что
приложение показало. Контролы при этом не привязываются к странице, и
`ParlamentApp._push` их не трогает — ровно как при сборке экрана.
"""

from __future__ import annotations

import asyncio
from typing import Any


class FakeWindow:
    width = height = min_width = min_height = 0


class FakePage:
    """Минимальная замена `ft.Page` — ровно та часть, что использует приложение."""

    def __init__(self):
        self.title = ""
        self.bgcolor = None
        self.padding = self.spacing = None
        self.fonts: dict[str, str] = {}
        self.theme = None
        self.theme_mode = None
        self.window = FakeWindow()
        self.services: list[Any] = []
        self.controls: list[Any] = []
        self.dialogs: list[Any] = []       # всё, что показывали, по порядку
        self.update_count = 0
        self.tasks: list[Any] = []

    # -- то, что вызывает приложение ---------------------------------------

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.update_count += 1

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, handler, *args):
        """Выполняет корутину сразу — тесту не нужен цикл событий."""
        result = handler(*args)
        if asyncio.iscoroutine(result):
            return asyncio.get_event_loop().run_until_complete(result)
        return result

    # -- помощники для проверок --------------------------------------------

    @property
    def dialog(self):
        """Верхний открытый диалог."""
        return self.dialogs[-1] if self.dialogs else None

    @property
    def snackbars(self) -> list:
        import flet as ft
        return [d for d in self.dialogs if isinstance(d, ft.SnackBar)]

    @property
    def last_toast(self) -> str | None:
        bars = self.snackbars
        return bars[-1].content.value if bars else None


#: Поля, в которых Flet держит вложенные контролы.
_CHILD_FIELDS = ("content", "controls", "actions", "rows", "columns", "cells", "title", "items")


def walk(control) -> list:
    """Обходит дерево контролов вглубь — для поиска в тестах."""
    import flet as ft

    found = [control]
    for attribute in _CHILD_FIELDS:
        value = getattr(control, attribute, None)
        children = value if isinstance(value, list) else [value]
        for child in children:
            if isinstance(child, ft.Control):
                found.extend(walk(child))
    return found


def texts(control) -> list[str]:
    """Все строковые значения `Text` в поддереве."""
    import flet as ft
    return [c.value for c in walk(control)
            if isinstance(c, ft.Text) and isinstance(c.value, str)]


def find(control, predicate):
    """Первый контрол в поддереве, удовлетворяющий условию."""
    for candidate in walk(control):
        if predicate(candidate):
            return candidate
    return None


def find_all(control, predicate) -> list:
    return [c for c in walk(control) if predicate(c)]
