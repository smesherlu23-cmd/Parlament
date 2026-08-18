"""Точка входа приложения «Парламент».

    python main.py

Логика и интерфейс работают в одном процессе Python: Flet рисует окно,
`parlament.service` держит данные и пишет их в файл проекта.
"""

from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

from parlament.service import ParlamentService
from parlament.store import StoreError
from parlament.ui.app import ParlamentApp
from parlament.ui.theme import ASSETS_DIR

APP_NAME = "Parlament"
PROJECT_FILE = "parlament.parlament.json"


def default_data_file() -> Path:
    """Файл проекта, который открывается при старте."""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / APP_NAME / PROJECT_FILE


def main(page: ft.Page) -> None:
    service = ParlamentService(default_data_file())

    startup_error: str | None = None
    try:
        service.bootstrap()
    except StoreError as error:
        # Испорченный файл не должен мешать запуску: стартуем с чистого
        # проекта и говорим об этом.
        service.project = service.project.empty()
        startup_error = f"{error} Приложение открыто с чистым проектом."

    app = ParlamentApp(page, service)
    app.build()

    if startup_error:
        app.toast(startup_error, error=True)


if __name__ == "__main__":
    ft.run(main, assets_dir=str(ASSETS_DIR))
