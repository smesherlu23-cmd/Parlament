# Бэкенд «Парламента»

Чистый Python 3.9+ без сторонних зависимостей: только стандартная библиотека,
поэтому PyInstaller собирает его в один exe без дополнительной настройки.

## Слои

| Модуль | Ответственность |
|---|---|
| `parlament/model.py` | Данные: `Party`, `Convocation`, `Project`; сериализация в JSON |
| `parlament/store.py` | Файл проекта: чтение и атомарная запись |
| `parlament/service.py` | Операции и валидация; автосохранение после каждой мутации |
| `parlament/rpc.py` | Протокол с Electron: JSON-строки через stdin/stdout |
| `main.py` | Точка входа, разбор аргументов |

## Запуск вручную

```bash
python backend/main.py --data-file /путь/к/проекту.parlament.json
```

Дальше можно слать запросы построчно:

```json
{"id": 1, "method": "party.create", "params": {"name": "Народный союз", "color": "#0088b0", "abbr": "НС"}}
```

## Тесты

```bash
python -m unittest discover -s backend/tests
```
