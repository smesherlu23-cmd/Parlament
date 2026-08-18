"""Бизнес-логика приложения «Парламент».

Слой данных не знает про интерфейс: `model` описывает данные, `service` —
операции над ними и проверки, `store` — файл проекта. Всё, что связано с Flet,
лежит в подпакете `ui`.
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
