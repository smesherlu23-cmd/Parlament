"""Бизнес-логика приложения «Парламент».

Пакет не зависит от Electron и от способа связи с ним: `model` описывает
данные, `service` — операции над ними, `store` — файл проекта, `rpc` —
транспорт (JSON-строки через stdin/stdout).
"""

from .model import Party, Convocation, Project, DEFAULT_TOTAL_SEATS
from .service import ParlamentService, ValidationError

__all__ = [
    "Party",
    "Convocation",
    "Project",
    "ParlamentService",
    "ValidationError",
    "DEFAULT_TOTAL_SEATS",
]
