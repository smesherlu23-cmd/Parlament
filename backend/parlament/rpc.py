"""Транспорт Electron ↔ Python: JSON-сообщения построчно через stdin/stdout.

Выбран stdio, а не локальный HTTP-сервер: нет занятого порта, нет запроса
брандмауэра Windows при первом запуске и нет постороннего слушателя на машине
пользователя. Бэкенд использует только стандартную библиотеку, поэтому
PyInstaller собирает его в один exe без лишних зависимостей.

Формат запроса:   {"id": 7, "method": "party.create", "params": {...}}
Формат ответа:    {"id": 7, "ok": true,  "result": {...}}
                  {"id": 7, "ok": false, "error": {"kind": "...", "message": "..."}}

Каждое сообщение — одна строка. Диагностика пишется в stderr, чтобы не
мешать протоколу.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from .service import ParlamentService, ValidationError
from .store import StoreError


class Rpc:
    """Разбирает строки stdin, вызывает методы сервиса, пишет ответы в stdout."""

    def __init__(self, service: ParlamentService):
        self.service = service
        self.methods: dict[str, Callable[[dict], Any]] = {
            "app.ping": lambda p: {"pong": True},
            "state.get": lambda p: self.service.state(),

            "project.new": self._project_new,
            "project.open": self._project_open,
            "project.save": self._project_save,
            "project.saveAs": self._project_save_as,

            "party.create": self._party_create,
            "party.update": self._party_update,
            "party.delete": self._party_delete,
            "party.usage": self._party_usage,

            "seats.set": self._seats_set,
            "seats.setAll": self._seats_set_all,
            "seats.reset": self._seats_reset,

            "convocation.rename": self._convocation_rename,
            "convocation.fix": self._convocation_fix,
        }

    # -- цикл обработки -----------------------------------------------------

    def serve(self, stdin=None, stdout=None) -> None:
        source = stdin if stdin is not None else sys.stdin
        sink = stdout if stdout is not None else sys.stdout
        for line in source:
            line = line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()

    def handle_line(self, line: str) -> dict:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return _error(None, "protocol", "Получено сообщение, не являющееся JSON.")

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        handler = self.methods.get(method)
        if handler is None:
            return _error(request_id, "protocol", f"Неизвестный метод: {method}")

        try:
            result = handler(params)
        except ValidationError as exc:
            return _error(request_id, "validation", str(exc))
        except StoreError as exc:
            return _error(request_id, "storage", str(exc))
        except Exception as exc:  # noqa: BLE001 — падение бэкенда не должно ронять окно
            traceback.print_exc(file=sys.stderr)
            return _error(request_id, "internal", f"Внутренняя ошибка: {exc}")

        return {"id": request_id, "ok": True, "result": result}

    # -- методы -------------------------------------------------------------
    # Каждая мутация возвращает полное состояние: интерфейс перерисовывается
    # из одного источника правды и не может разойтись с бэкендом.

    def _with_state(self, extra: dict | None = None) -> dict:
        payload = {"state": self.service.state()}
        if extra:
            payload.update(extra)
        return payload

    def _project_new(self, params: dict) -> dict:
        self.service.new_project()
        return self._with_state()

    def _project_open(self, params: dict) -> dict:
        self.service.open_project(_require(params, "path"))
        return self._with_state()

    def _project_save(self, params: dict) -> dict:
        self.service.save_project()
        return self._with_state()

    def _project_save_as(self, params: dict) -> dict:
        self.service.save_project_as(_require(params, "path"))
        return self._with_state()

    def _party_create(self, params: dict) -> dict:
        party = self.service.create_party(
            name=params.get("name"), color=params.get("color"), abbr=params.get("abbr", "")
        )
        return self._with_state({"partyId": party.id})

    def _party_update(self, params: dict) -> dict:
        party = self.service.update_party(
            party_id=_require(params, "id"),
            name=params.get("name"),
            color=params.get("color"),
            abbr=params.get("abbr", ""),
        )
        return self._with_state({"partyId": party.id})

    def _party_delete(self, params: dict) -> dict:
        self.service.delete_party(_require(params, "id"))
        return self._with_state()

    def _party_usage(self, params: dict) -> dict:
        return {"usage": self.service.party_usage(_require(params, "id"))}

    def _seats_set(self, params: dict) -> dict:
        self.service.set_seats(
            convocation_id=_require(params, "convocationId"),
            party_id=_require(params, "partyId"),
            seats=params.get("seats", 0),
        )
        return self._with_state()

    def _seats_set_all(self, params: dict) -> dict:
        self.service.set_all_seats(
            convocation_id=_require(params, "convocationId"),
            seats=params.get("seats") or {},
        )
        return self._with_state()

    def _seats_reset(self, params: dict) -> dict:
        self.service.reset_seats(_require(params, "convocationId"))
        return self._with_state()

    def _convocation_rename(self, params: dict) -> dict:
        self.service.rename_convocation(_require(params, "id"), params.get("name"))
        return self._with_state()

    def _convocation_fix(self, params: dict) -> dict:
        fresh = self.service.fix_convocation(params.get("name"))
        return self._with_state({"convocationId": fresh.id})


def _require(params: dict, key: str):
    if key not in params or params[key] in (None, ""):
        raise ValidationError(f"Не передан обязательный параметр «{key}».")
    return params[key]


def _error(request_id, kind: str, message: str) -> dict:
    return {"id": request_id, "ok": False, "error": {"kind": kind, "message": message}}
