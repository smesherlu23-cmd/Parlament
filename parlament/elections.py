"""Расчёт выборов: как голоса в округе превращаются в места.

Правило игры (по уточнению заказчика): места округа делятся между партиями
в процентном соотношении набранных голосов, кто набрал больше — забирает
большинство мест округа. Если борьбы не было (голоса есть только у одной
партии), победитель забирает все места округа — это получается само собой,
отдельной ветки в коде не требует.

Способ деления — метод наибольших остатков (квота Хэйра): каждой партии
достаётся целая часть от её доли, а оставшиеся места уходят тем, у кого
самый большой дробный хвост. Из распространённых методов он ближе всех к
буквальному «в процентном соотношении»: Д'Ондт, например, системно
подсуживает крупным партиям и от чистых процентов заметно отклоняется.
"""

from __future__ import annotations

#: Ничьи (одинаковые остатки, одинаковые голоса) разрешаются по порядку
#: партий во входном словаре — то есть по порядку справочника, раз вызывающая
#: сторона строит `votes` из него. Брать «последним доводом» id партии нельзя:
#: id случайный, и победитель ничьей выглядел бы взятым с потолка.


def allocate_seats(votes: dict[str, int], seats: int) -> dict[str, int]:
    """Делит `seats` мест округа между партиями пропорционально голосам.

    :param votes: голоса по партиям — `{party_id: голоса}`; нули и минусы
                  игнорируются, партия без голосов мест не получает.
    :param seats: сколько мест разыгрывается в округе.
    :return: `{party_id: места}` — только партии, получившие хотя бы одно
             место. Сумма всегда ровно `seats`, если голоса вообще есть.

    Пустой ввод (ни одного голоса) даёт пустой результат: места округа
    остаются нераспределёнными, а не раздаются наугад.
    """
    if seats <= 0:
        return {}

    clean = {pid: int(v) for pid, v in votes.items() if int(v) > 0}
    total = sum(clean.values())
    if not clean or total <= 0:
        return {}

    # Целая часть доли — гарантированные места.
    exact = {pid: v * seats / total for pid, v in clean.items()}
    result = {pid: int(share) for pid, share in exact.items()}

    # Остаток раздаём по величине дробного хвоста: сначала самый длинный.
    left = seats - sum(result.values())
    if left > 0:
        rank = _register_rank(clean)
        order = sorted(
            clean,
            key=lambda pid: (exact[pid] - int(exact[pid]), clean[pid], rank[pid]),
            reverse=True,
        )
        for pid in order[:left]:
            result[pid] += 1

    return {pid: n for pid, n in result.items() if n > 0}


def _register_rank(votes: dict[str, int]) -> dict[str, float]:
    """Вес партии при ничьей: чем раньше в справочнике, тем больше.

    Сортировки ниже идут по убыванию, поэтому первая партия должна получить
    наибольшее число — берём позицию с минусом.
    """
    return {pid: -index for index, pid in enumerate(votes)}


def district_winner(votes: dict[str, int], seats: int) -> str | None:
    """Партия, которой достаётся округ, — по местам, а при равенстве по голосам.

    Именно её цветом красится маркер округа на карте. `None`, если по округу
    ещё нет данных.
    """
    allocation = allocate_seats(votes, seats)
    if not allocation:
        return None
    clean = {pid: int(v) for pid, v in votes.items() if int(v) > 0}
    rank = _register_rank(clean)
    return max(allocation, key=lambda pid: (allocation[pid], clean.get(pid, 0), rank[pid]))


def allocate_all(
    votes_by_district: dict[str, dict[str, int]],
    district_seats: dict[str, int],
) -> dict[str, dict[str, int]]:
    """Считает места по всем округам разом: `{district_id: {party_id: места}}`."""
    return {
        district_id: allocate_seats(votes, district_seats.get(district_id, 0))
        for district_id, votes in votes_by_district.items()
        if district_id in district_seats
    }


def totals_by_party(allocation: dict[str, dict[str, int]]) -> dict[str, int]:
    """Сводит места всех округов в общий состав парламента."""
    totals: dict[str, int] = {}
    for per_party in allocation.values():
        for party_id, seats in per_party.items():
            totals[party_id] = totals.get(party_id, 0) + seats
    return totals
