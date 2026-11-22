"""Exact reachable bounds for shared-mode synchronous first passage.

Within one tick both benches draw the *same* anonymous mode (``high`` or
``low``, each with weight one half) and then, given that mode and the whole
public past, each bench's next step must keep exactly its own current row
marginal.  Any joint coupling of the two projections is allowed.  Therefore
the success contributed by a tick is a bilinear form whose extreme values are
optimal-transport costs between the two row distributions.

Neither future draws nor future couplings may influence the current tick, so
the extremes obey a backward dynamic program over exact rationals.  The value
at a live pair is the least / greatest attainable continuation probability
from that pair over all admissible processes.
"""
from collections import deque
from fractions import Fraction

from intake.modes import ModeEvidence, ModeRows


ZERO = Fraction(0)
ONE = Fraction(1)
TWO = Fraction(2)
PASS = 'pass'
FAIL = 'fail'
LIVE = 'live'

Tag = tuple[str, int | None]


class _MinCostTransport:
    """Successive-shortest-path min-cost flow with exact rational costs."""

    def __init__(self, size: int):
        self._graph: list[list[list]] = [[] for _ in range(size)]

    def add(self, source: int, target: int, capacity: Fraction, cost: Fraction) -> None:
        self._graph[source].append([target, len(self._graph[target]), capacity, cost])
        self._graph[target].append([source, len(self._graph[source]) - 1, ZERO, -cost])

    def solve(self, source: int, sink: int, need: Fraction) -> Fraction:
        graph = self._graph
        size = len(graph)
        total = ZERO
        cost = ZERO
        while total < need:
            distance: list[Fraction | None] = [None] * size
            previous: list[tuple[int, int] | None] = [None] * size
            queued = [False] * size
            distance[source] = ZERO
            queue = deque([source])
            queued[source] = True
            while queue:
                node = queue.popleft()
                queued[node] = False
                base = distance[node]
                for position, edge in enumerate(graph[node]):
                    target, _, capacity, edge_cost = edge
                    if capacity <= 0:
                        continue
                    candidate = base + edge_cost
                    if distance[target] is None or candidate < distance[target]:
                        distance[target] = candidate
                        previous[target] = (node, position)
                        if not queued[target]:
                            queue.append(target)
                            queued[target] = True
            if distance[sink] is None:
                raise ValueError('the two row distributions do not balance')
            push = need - total
            node = sink
            while node != source:
                origin, position = previous[node]
                push = min(push, graph[origin][position][2])
                node = origin
            node = sink
            while node != source:
                origin, position = previous[node]
                edge = graph[origin][position]
                edge[2] -= push
                graph[node][edge[1]][2] += push
                node = origin
            total += push
            cost += push * distance[sink]
        return cost


def _transport(left: dict[Tag, Fraction], right: dict[Tag, Fraction],
               cost, maximize: bool) -> Fraction:
    """Least/greatest cost coupling two probability rows with fixed marginals."""
    sources = [(tag, weight) for tag, weight in sorted(left.items(), key=lambda item: item[0])
               if weight > 0]
    targets = [(tag, weight) for tag, weight in sorted(right.items(), key=lambda item: item[0])
               if weight > 0]
    if sum((weight for _, weight in sources), ZERO) != ONE:
        raise ValueError('a left row must conserve probability')
    if sum((weight for _, weight in targets), ZERO) != ONE:
        raise ValueError('a right row must conserve probability')
    origin_node = len(sources) + len(targets)
    sink_node = origin_node + 1
    network = _MinCostTransport(sink_node + 1)
    sign = -1 if maximize else 1
    for position, (_, weight) in enumerate(sources):
        network.add(origin_node, position, weight, ZERO)
    for offset, (_, weight) in enumerate(targets):
        network.add(len(sources) + offset, sink_node, weight, ZERO)
    for i, (_, left_weight) in enumerate(sources):
        for j, (_, right_weight) in enumerate(targets):
            network.add(i, len(sources) + j, min(left_weight, right_weight),
                        sign * cost(sources[i][0], targets[j][0]))
    return sign * network.solve(origin_node, sink_node, ONE)


def _row(rows: ModeRows, position: int) -> dict[Tag, Fraction]:
    destinations: dict[Tag, Fraction] = {}
    for target, weight in rows.continuation[position]:
        tag = (LIVE, target)
        destinations[tag] = destinations.get(tag, ZERO) + weight
    destinations[(PASS, None)] = rows.direct_pass[position]
    destinations[(FAIL, None)] = rows.direct_fail[position]
    return destinations


def _tick_value(left_row: dict[Tag, Fraction], right_row: dict[Tag, Fraction],
                table: dict[tuple[int, int], Fraction], maximize: bool) -> Fraction:
    def cost(here: Tag, there: Tag) -> Fraction:
        here_kind, here_state = here
        there_kind, there_state = there
        if here_kind == PASS and there_kind == PASS:
            return ONE
        if here_kind != LIVE or there_kind != LIVE:
            return ZERO
        return table[(here_state, there_state)]

    return _transport(left_row, right_row, cost, maximize)


def reachable_bounds(left: ModeEvidence, right: ModeEvidence,
                     horizon: int) -> tuple[Fraction, Fraction]:
    """Exact least and greatest shared-mode synchronous mass up to ``horizon``."""
    if type(horizon) is not int or horizon < 1:
        raise ValueError('horizon must be a positive integer')
    low = {(a, b): ZERO for a in left.live for b in right.live}
    high = dict(low)
    left_rows = {mode: [_row(mode_rows, position) for position in range(len(left.live))]
                 for mode, mode_rows in (('high', left.high), ('low', left.low))}
    right_rows = {mode: [_row(mode_rows, position) for position in range(len(right.live))]
                  for mode, mode_rows in (('high', right.high), ('low', right.low))}
    left_position = {state: index for index, state in enumerate(left.live)}
    right_position = {state: index for index, state in enumerate(right.live)}

    for _ in range(horizon, 0, -1):
        new_low: dict[tuple[int, int], Fraction] = {}
        new_high: dict[tuple[int, int], Fraction] = {}
        for a in left.live:
            for b in right.live:
                low_total = ZERO
                high_total = ZERO
                for mode in ('high', 'low'):
                    left_row = left_rows[mode][left_position[a]]
                    right_row = right_rows[mode][right_position[b]]
                    low_total += _tick_value(left_row, right_row, low, maximize=False)
                    high_total += _tick_value(left_row, right_row, high, maximize=True)
                new_low[(a, b)] = low_total / TWO
                new_high[(a, b)] = high_total / TWO
        if new_low == low and new_high == high:
            # The stationary recurrence has reached its fixed point, so every
            # longer horizon keeps these values exactly.
            break
        low, high = new_low, new_high
    return low[(left.initial, right.initial)], high[(left.initial, right.initial)]
