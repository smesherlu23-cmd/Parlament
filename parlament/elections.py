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

from dataclasses import dataclass

#: Ничьи (одинаковые остатки, одинаковые голоса) разрешаются по порядку
#: партий во входном словаре — то есть по порядку справочника, раз вызывающая
#: сторона строит `votes` из него. Брать «последним доводом» id партии нельзя:
#: id случайный, и победитель ничьей выглядел бы взятым с потолка.


def allocate_seats(votes: dict[str, float], seats: int) -> dict[str, int]:
    """Делит `seats` мест округа между партиями пропорционально голосам.

    :param votes: вес партий в округе — `{party_id: число}`. Это либо живые
                  голоса, либо итог броска с модификаторами; дробные значения
                  допустимы. Нули и минусы игнорируются: партия без голосов
                  мест не получает.
    :param seats: сколько мест разыгрывается в округе.
    :return: `{party_id: места}` — только партии, получившие хотя бы одно
             место. Сумма всегда ровно `seats`, если голоса вообще есть.

    Пустой ввод (ни одного голоса) даёт пустой результат: места округа
    остаются нераспределёнными, а не раздаются наугад.
    """
    if seats <= 0:
        return {}

    # Дроби не округляем: вес партии в округе получается из среднего числа
    # очков поддержки по НП и целым почти никогда не бывает. Округление здесь
    # съедало бы как раз ту разницу, ради которой поддержка и считается.
    clean = {pid: float(v) for pid, v in votes.items() if float(v) > 0}
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


def _register_rank(votes: dict[str, float]) -> dict[str, float]:
    """Вес партии при ничьей: чем раньше в справочнике, тем больше.

    Сортировки ниже идут по убыванию, поэтому первая партия должна получить
    наибольшее число — берём позицию с минусом.
    """
    return {pid: -index for index, pid in enumerate(votes)}


def district_winner(votes: dict[str, float], seats: int) -> str | None:
    """Партия, которой достаётся округ, — по местам, а при равенстве по голосам.

    Именно её цветом красится маркер округа на карте. `None`, если по округу
    ещё нет данных.
    """
    allocation = allocate_seats(votes, seats)
    if not allocation:
        return None
    # Дроби не округляем: вес партии в округе получается из среднего числа
    # очков поддержки по НП и целым почти никогда не бывает. Округление здесь
    # съедало бы как раз ту разницу, ради которой поддержка и считается.
    clean = {pid: float(v) for pid, v in votes.items() if float(v) > 0}
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


# -- бросок и модификаторы ---------------------------------------------------
#
# Голоса в округе не вводятся руками, а разыгрываются: каждой партии выпадает
# число 1–10, к нему прибавляются модификаторы, и уже эти итоговые числа
# разносятся по партиям пропорционально. То есть «3, 5, 7» при сумме 15 дают
# 20 %, 33,3 % и 46,7 % голосов.

#: Границы броска.
MIN_ROLL = 1
MAX_ROLL = 10

#: Сколько очков неформальной популярности несёт один населённый пункт.
#: Эти очки игроки делят между партиями, отсюда и берётся модификатор.
SETTLEMENT_SUPPORT = 6

#: Прибавка за потраченное на агитацию действие.
AGITATION_BONUS = 1


@dataclass(frozen=True)
class PartyRoll:
    """Расклад одной партии в одном округе — с разбивкой по слагаемым.

    Хранится целиком, а не одним итогом: ведущему нужно видеть, из чего
    сложился результат, а пересчитать это потом будет не из чего — бросок
    случайный и не повторяется.
    """

    roll: int = 0
    #: Средняя поддержка по НП округа: сумма очков партии, делённая на число
    #: населённых пунктов. Дробная величина — так и задумано.
    support: float = 0.0
    #: Свободный бонус за дебаты; отрицательный — штраф.
    debate: float = 0.0
    agitation: bool = False

    @property
    def total(self) -> float:
        """Итоговое число партии в округе, не ниже нуля.

        Ноль означает, что партия голосов в округе не получила вовсе:
        модификаторы способны увести сумму в минус, но отрицательный «вес»
        сломал бы пропорцию — он вычитал бы голоса у остальных.
        """
        bonus = AGITATION_BONUS if self.agitation else 0
        return max(0.0, self.roll + self.support + self.debate + bonus)

    def to_dict(self) -> dict:
        return {"roll": self.roll, "support": self.support,
                "debate": self.debate, "agitation": self.agitation}

    @staticmethod
    def from_dict(raw: dict) -> "PartyRoll":
        def number(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        return PartyRoll(
            roll=int(number(raw.get("roll"), 0)),
            support=number(raw.get("support"), 0.0),
            debate=number(raw.get("debate"), 0.0),
            agitation=bool(raw.get("agitation")),
        )


def support_modifier(points: float, settlements: int) -> float:
    """Модификатор поддержки: очки партии в округе делённые на число НП.

    Округов без населённых пунктов быть не должно, но делить на ноль на
    всякий случай не станем — модификатор тогда просто нулевой.
    """
    if settlements <= 0:
        return 0.0
    return points / settlements


def roll_dice(rng) -> int:
    """Бросок 1–10. `rng` передаётся снаружи, чтобы тесты были повторяемы."""
    return rng.randint(MIN_ROLL, MAX_ROLL)


def weights(rolls: dict[str, PartyRoll]) -> dict[str, float]:
    """Итоговые числа партий — то, что дальше делится на места и проценты."""
    return {party_id: r.total for party_id, r in rolls.items() if r.total > 0}


def shares(rolls: dict[str, PartyRoll]) -> dict[str, float]:
    """Доли голосов в процентах — как их видит ведущий в таблице."""
    totals = weights(rolls)
    overall = sum(totals.values())
    if overall <= 0:
        return {}
    return {party_id: value / overall * 100 for party_id, value in totals.items()}
