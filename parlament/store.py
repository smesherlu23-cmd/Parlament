"""Чтение и запись файла проекта.

Формат — JSON в UTF-8. Запись атомарная (через временный файл рядом с целевым
и `os.replace`), чтобы сбой посреди сохранения не оставил обрезанный файл:
проект пользователя — единственная копия его игры.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .model import Project

PROJECT_EXTENSION = ".parlament.json"


class StoreError(Exception):
    """Файл не удалось прочитать или записать — сообщение готово для показа."""


def load(path: str | os.PathLike) -> Project:
    file = Path(path)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StoreError(f"Файл не найден: {file}") from None
    except json.JSONDecodeError as exc:
        raise StoreError(f"Файл не является корректным JSON ({exc.lineno}:{exc.colno}).") from None
    except OSError as exc:
        raise StoreError(f"Не удалось прочитать файл: {exc.strerror or exc}") from None

    try:
        return Project.from_dict(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise StoreError(f"Файл проекта повреждён: {exc}") from None


def save(project: Project, path: str | os.PathLike) -> None:
    file = Path(path)
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        # Временный файл кладём в ту же папку: os.replace атомарен только
        # в пределах одной файловой системы.
        handle, temp_path = tempfile.mkstemp(dir=str(file.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, file)
        except BaseException:
            # Не оставляем мусор рядом с проектом, если запись не удалась.
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise StoreError(f"Не удалось сохранить файл: {exc.strerror or exc}") from None


def set_aside(path: str | os.PathLike) -> Path | None:
    """Отодвигает нечитаемый файл проекта в сторону, возвращая новое имя.

    Программа не может открыть такой файл, но и затирать его нельзя: это
    единственная копия игры, и вручную из неё нередко можно вытащить всё.
    Без этого первое же действие в чистом проекте сохранялось бы поверх
    испорченного файла — и спасать было бы уже нечего.
    """
    file = Path(path)
    if not file.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = file.with_name(f"{file.name}.broken-{stamp}")
    try:
        os.replace(file, target)
    except OSError:
        return None
    return target
