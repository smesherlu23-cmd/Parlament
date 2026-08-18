"""Точка входа бэкенда.

Запускается Electron'ом как дочерний процесс:

    python backend/main.py --data-file "<путь к файлу проекта>"

В собранном приложении это тот же код, упакованный PyInstaller'ом в
`parlament-backend.exe` — интерфейс запуска не меняется.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parlament.rpc import Rpc  # noqa: E402
from parlament.service import ParlamentService  # noqa: E402
from parlament.store import StoreError  # noqa: E402


def default_data_file() -> Path:
    """Путь по умолчанию, если Electron его не передал (запуск вручную)."""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / "Parlament" / "parlament.parlament.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Бэкенд приложения «Парламент».")
    parser.add_argument("--data-file", dest="data_file", default=None,
                        help="Файл проекта, открываемый при старте.")
    args = parser.parse_args(argv)

    data_file = Path(args.data_file) if args.data_file else default_data_file()
    service = ParlamentService(data_file)

    # Windows-консоль по умолчанию не в UTF-8, а протокол обязан быть UTF-8.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline="\n")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")

    try:
        service.bootstrap()
    except StoreError as exc:
        # Испорченный файл не должен мешать запуску: стартуем с пустого
        # проекта и говорим интерфейсу, что случилось.
        service.project = service.project.empty()
        stdout.write(json.dumps(
            {"id": None, "event": "bootstrap-error", "error": {"kind": "storage", "message": str(exc)}},
            ensure_ascii=False) + "\n")
        stdout.flush()

    stdout.write(json.dumps({"id": None, "event": "ready", "state": service.state()},
                            ensure_ascii=False) + "\n")
    stdout.flush()

    Rpc(service).serve(stdin=stdin, stdout=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
