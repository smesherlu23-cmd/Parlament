"""Как состав зала читается блоками: партия сама по себе или коалиция.

Коалиция — не новая партия, а договор нынешних: своих мест у неё нет, её вес
складывается из мест участников. Поэтому на схеме зала блок рисуется не одним
цветом, а плёнкой поверх мест: сквозь неё видно, из чьих цветов он собран.

Здесь считается только порядок и состав блоков — что и в каком порядке
показывать. Отрисовка (`ui.seat_chart`, `ui.export`) берёт результат как есть,
поэтому окно и картинка в PNG всегда сходятся.

Порядок важен не только для красоты: места на схеме красятся подряд, слева
направо, и участники одного блока обязаны идти вплотную друг к другу — иначе
плёнка накрыла бы чужие места, а на дуге вместо одного блока получились бы
разбросанные пятна.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Member:
    """Партия внутри блока — со своими местами и своим цветом."""

    party_id: str
    name: str
    color: str
    seats: int
    #: Доля голосов по стране, в процентах. `None` — выборов не было и
    #: голосов не существует: места набраны руками.
    votes: float | None = None


@dataclass(frozen=True)
class Bloc:
    """Колонка зала: либо одинокая партия, либо коалиция из нескольких.

    У одинокой партии `film` пуст, а `members` — она сама: так обоим случаям
    достаётся одна форма, и вызывающей стороне не приходится их различать
    везде, где нужны просто места по цветам.
    """

    name: str
    color: str
    seats: int
    #: Цвет плёнки над местами блока. `None` у одинокой партии: накрывать
    #: партию плёнкой её же цвета незачем.
    film: str | None
    members: tuple[Member, ...]
    #: Заполнен только у коалиции — по нему интерфейс находит, что править.
    coalition_id: str | None = None

    @property
    def is_coalition(self) -> bool:
        return self.coalition_id is not None

    @property
    def votes(self) -> float | None:
        """Доля голосов блока — сумма долей участников.

        `None`, если выборов не было: голосов тогда не существует вовсе, и
        показывать вместо них ноль значило бы соврать.
        """
        known = [m.votes for m in self.members if m.votes is not None]
        return sum(known) if known else None


def blocs(parties, seats: dict[str, int], coalitions=(),
          votes: dict[str, float] | None = None) -> list[Bloc]:
    """Состав зала блоками, крупнейший слева.

    :param parties: партии справочника — их порядок решает ничьи.
    :param seats: `{party_id: мест}` текущего созыва.
    :param coalitions: блоки созыва (`model.Coalition`).
    :param votes: `{party_id: % голосов}`, если выборы были. Места и голоса
                  расходятся — на то и показывают оба числа.

    Партии и блоки без мест выпадают: показывать в зале нечего, а в легенде
    они заняли бы строку ради нуля. Партия, попавшая в коалицию, отдельной
    колонкой не идёт — иначе её места посчитались бы дважды.
    """
    rank = {party.id: index for index, party in enumerate(parties)}
    by_id = {party.id: party for party in parties}

    def member(party_id: str) -> Member:
        party = by_id[party_id]
        return Member(party.id, party.name, party.color, seats.get(party.id, 0),
                      (votes or {}).get(party.id) if votes else None)

    result: list[Bloc] = []
    taken: set[str] = set()

    for coalition in coalitions:
        # Порядок участников — как в справочнике, а не как их отмечали
        # галочками: на схеме блок должен выглядеть одинаково при каждой
        # пересборке, а не зависеть от того, кого добавили первым.
        inside = sorted((pid for pid in dict.fromkeys(coalition.members)
                         if pid in by_id and pid not in taken),
                        key=lambda pid: (-seats.get(pid, 0), rank[pid]))
        taken.update(inside)
        members = tuple(member(pid) for pid in inside if seats.get(pid, 0) > 0)
        if not members:
            continue
        result.append(Bloc(
            name=coalition.name,
            color=coalition.color,
            seats=sum(m.seats for m in members),
            film=coalition.color,
            members=members,
            coalition_id=coalition.id,
        ))

    for party in parties:
        if party.id in taken or seats.get(party.id, 0) <= 0:
            continue
        one = member(party.id)
        result.append(Bloc(name=party.name, color=party.color, seats=one.seats,
                          film=None, members=(one,)))

    # Крупнейший блок уходит влево; при равенстве вперёд идёт тот, чья первая
    # партия раньше в справочнике — «последним доводом» брать id нельзя, он
    # случайный, и порядок выглядел бы взятым с потолка.
    result.sort(key=lambda b: (-b.seats, rank[b.members[0].party_id]))
    return result


def chart_distribution(blocs_: list[Bloc]) -> list[tuple[str, int, str | None]]:
    """Места для схемы зала: цвет партии, сколько мест, чем накрыты сверху.

    Разворачивает блоки в плоский список в порядке отрисовки — участники
    одного блока идут подряд, поэтому плёнка ложится на сплошной сектор.
    """
    return [(m.color, m.seats, bloc.film) for bloc in blocs_ for m in bloc.members]
