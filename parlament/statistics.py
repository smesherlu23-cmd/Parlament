"""Статистика выборов: что можно вычитать из уже разыгранных итогов.

Отдельно от `elections` — там правила игры, здесь чтение результата. Ничего
нового этот модуль не разыгрывает и ничего не меняет: всё считается из
`Convocation.results`, которые уже лежат в проекте, поэтому одна и та же
статистика получается и на экране, и в выгрузке, и через год у архивного
созыва.

Чистые функции без Flet и без сервиса: на вход — готовые таблицы, на выход —
строки, которые остаётся только показать. Так их видно тестами по отдельности
от интерфейса.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import elections
from .elections import PartyResult


@dataclass(frozen=True)
class IslandRow:
    """Остров целиком: сколько там мандатов, людей и кто их взял."""

    name: str
    #: Сколько мандатов разыгрывается на острове.
    seats: int
    #: Сколько человек на острове живёт — вес его голосов (см. `district_seed`).
    population: float
    #: Мандаты партий на этом острове, `{party_id: мандаты}`.
    by_party: dict[str, int]
    #: Доли голосов партий на острове, в процентах, взвешенные по населению
    #: округов — тем же способом, что и доля по стране в `service.vote_shares`.
    shares: dict[str, float]

    @property
    def people_per_seat(self) -> float:
        """Сколько человек стоит за одним мандатом острова.

        Мандаты по островам разложены неровно, и это число показывает
        насколько: где оно меньше, там голос избирателя весит больше, а
        усилие партии дешевле превращается в мандат.
        """
        return self.population / self.seats if self.seats else 0.0


@dataclass(frozen=True)
class Battleground:
    """Округ глазами одной партии — есть ли тут за что бороться."""

    district_id: str
    name: str
    island: str
    seats: int
    #: Доля партии в округе, в процентах.
    share: float
    #: Кто взял округ и с какой долей. `None` — округ никому не достался.
    leader: str | None
    leader_share: float
    #: Сколько мандатов округа досталось самой партии.
    won: int
    #: Очки поддержки: свои, неразобранные и весь запас округа.
    points: int
    free: int
    capacity: int

    @property
    def gap(self) -> float:
        """Отставание от победителя округа, в процентных пунктах."""
        return max(0.0, self.leader_share - self.share)

    @property
    def missed_threshold(self) -> bool:
        """Доля есть, но мандатов не будет — не дотянули до барьера."""
        return 0.0 < self.share < elections.THRESHOLD_PERCENT


def island_rows(districts, results, allocation, populations) -> list[IslandRow]:
    """Сводка по каждому острову, в порядке появления островов на карте.

    :param districts: `(district_id, остров, мандатов)` по каждому округу.
    :param results: `{district_id: {party_id: PartyResult}}` — разбор выборов.
    :param allocation: `{district_id: {party_id: мандаты}}`.
    :param populations: `{district_id: человек}` — население округа.

    Доли острова считаются по населению округов, а не по их числу: округ на
    сорок тысяч человек и хутор на десять не равны, и усреднять их наравне
    значило бы приравнять хутор к столице.
    """
    order: list[str] = []
    seats: dict[str, int] = {}
    people: dict[str, float] = {}
    by_party: dict[str, dict[str, int]] = {}
    weighted: dict[str, dict[str, float]] = {}

    for district_id, island, count in districts:
        if island not in seats:
            order.append(island)
            seats[island] = 0
            people[island] = 0.0
            by_party[island] = {}
            weighted[island] = {}
        seats[island] += count
        population = float(populations.get(district_id, 0.0))
        people[island] += population

        for party_id, got in (allocation.get(district_id) or {}).items():
            by_party[island][party_id] = by_party[island].get(party_id, 0) + got
        for party_id, result in (results.get(district_id) or {}).items():
            weighted[island][party_id] = (weighted[island].get(party_id, 0.0)
                                          + result.share * population)

    rows = []
    for island in order:
        total = people[island]
        shares = ({party_id: value / total for party_id, value in weighted[island].items()}
                  if total > 0 else {})
        rows.append(IslandRow(name=island, seats=seats[island], population=total,
                              by_party=by_party[island], shares=shares))
    return rows


def battlegrounds(party_id: str, districts, results, allocation,
                  points, capacities) -> list[Battleground]:
    """Все округа глазами партии — по одной строке на округ.

    :param districts: `(district_id, название, остров, мандатов)`.
    :param points: `{district_id: {party_id: очки}}` — розданная поддержка.
    :param capacities: `{district_id: запас очков}`.

    Отбирать, где именно бороться, — дело вызывающей стороны: у «почти
    выиграли», «не хватило до барьера» и «никто не агитировал» разные
    условия, но данные одни и те же, и считать их трижды незачем.
    """
    rows = []
    for district_id, name, island, seats in districts:
        per_party = results.get(district_id) or {}
        shares = {pid: result.share for pid, result in per_party.items()}
        leader = max(shares, key=lambda pid: shares[pid], default=None)
        if leader is not None and shares[leader] <= 0:
            leader = None

        given = sum(max(0, value) for value in (points.get(district_id) or {}).values())
        capacity = int(capacities.get(district_id, 0))
        rows.append(Battleground(
            district_id=district_id, name=name, island=island, seats=seats,
            share=shares.get(party_id, 0.0),
            leader=leader,
            leader_share=shares.get(leader, 0.0) if leader else 0.0,
            won=(allocation.get(district_id) or {}).get(party_id, 0),
            points=(points.get(district_id) or {}).get(party_id, 0),
            free=max(0, capacity - given),
            capacity=capacity,
        ))
    return rows


#: Слагаемые, вклад которых в мандаты можно пересчитать задним числом.
#: Местная поправка сюда не входит: она в очках и уже сложена с живыми
#: очками внутри базы (см. `elections.boost_points`), а из одного разбора
#: обратно её не вынуть — для этого нужны очки всех партий округа и его
#: запас, которых в результате не сохранено.
FACTORS = ("national", "island", "wobble")


def attribution(results, district_seats) -> dict[str, dict[str, int]]:
    """Сколько мандатов дало или отняло каждое слагаемое — `{party: {слагаемое: ±}}`.

    Считается честным пересчётом: тот же дележ мест, но с занулённым
    слагаемым у всех сразу, и разница с настоящим составом — это и есть его
    вклад. Никакой оценки «на глазок»: если «настроение по стране» стоило
    партии девяти мандатов, значит без него она получила бы ровно на девять
    больше.

    Числа не обязаны складываться в разницу с нулём по всем слагаемым сразу:
    мандаты целые, барьер отсекает по долям, и два снятых слагаемых вместе
    могут сдвинуть округ не так, как каждое по отдельности. Это не ошибка
    счёта, а свойство дележа мест.
    """
    actual = _totals(results, district_seats)
    out: dict[str, dict[str, int]] = {}
    for factor in FACTORS:
        without = _totals(results, district_seats, drop=factor)
        for party_id in set(actual) | set(without):
            out.setdefault(party_id, {})[factor] = (actual.get(party_id, 0)
                                                    - without.get(party_id, 0))
    return out


def _totals(results, district_seats, drop: str | None = None) -> dict[str, int]:
    """Состав парламента по этому разбору, при желании без одного слагаемого."""
    weights = {}
    for district_id, per_party in results.items():
        if drop is None:
            trimmed = per_party
        else:
            trimmed = {pid: replace(result, **{drop: 0.0})
                       for pid, result in per_party.items()}
        share = elections.normalize_shares({pid: result.raw
                                            for pid, result in trimmed.items()})
        fresh = {pid: PartyResult(share=value) for pid, value in share.items()}
        passing = elections.weights(fresh)
        if passing:
            weights[district_id] = passing
    return elections.totals_by_party(elections.allocate_all(weights, district_seats))
