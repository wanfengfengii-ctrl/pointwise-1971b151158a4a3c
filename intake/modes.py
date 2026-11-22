"""V2 anonymous two-mode evidence: issuance and decoding.

A V2 package carries two equally weighted anonymous rows (``high`` and
``low``) over the same state identities.  Their per-destination average is
the fixed V1 revision, so the two modes are a refinement of that revision.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256

from intake.codec import (
    PackageFormatError,
    decode_body,
    decode_fraction,
    encode_body,
    encode_fraction,
)
from intake.pack import Package, PackageIdentity
from protocol.model import Protocol, Transition
from protocol.revision import RevisionStore
from protocol.validate import validate_protocol


ZERO = Fraction(0)
MODE_NAMES = ('high', 'low')
_BODY_KEYS = frozenset({
    'format', 'package_id', 'analysis_id', 'bench_id', 'revision_id',
    'initial', 'pass_terminal', 'fail_terminal', 'live', 'modes',
})
_MODE_KEYS = frozenset({'continuation', 'pass', 'fail'})


class InvalidModePackage(ValueError):
    """A V2 delivery or a V2 mode table is malformed."""


@dataclass(frozen=True, slots=True)
class ModeRows:
    """Anonymous rows for one mode, aligned with :attr:`ModeEvidence.live`."""

    continuation: tuple[tuple[tuple[int, Fraction], ...], ...]
    direct_pass: tuple[Fraction, ...]
    direct_fail: tuple[Fraction, ...]


@dataclass(frozen=True, slots=True)
class ModeEvidence:
    initial: int
    pass_terminal: int
    fail_terminal: int
    live: tuple[int, ...]
    high: ModeRows
    low: ModeRows

    def rows(self, mode: str) -> ModeRows:
        if mode == 'high':
            return self.high
        if mode == 'low':
            return self.low
        raise ValueError(f'unknown mode {mode!r}')


@dataclass(frozen=True, slots=True)
class ModeBody:
    package_id: str
    analysis_id: str
    bench_id: str
    revision_id: str
    evidence: ModeEvidence


def _is_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def _is_ascii(value: object) -> bool:
    if not _is_identifier(value):
        return False
    try:
        value.encode('ascii')
    except UnicodeEncodeError:
        return False
    return True


def _int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise InvalidModePackage(f'{label} must be a non-negative integer')
    return value


def _snapshot_modes(modes: Mapping[str, object]) -> dict[str, tuple[Transition, ...]]:
    if not isinstance(modes, Mapping):
        raise InvalidModePackage('mode table must be a mapping with high and low')
    if set(modes.keys()) != set(MODE_NAMES):
        raise InvalidModePackage('mode table must have exactly the high and low keys')
    snapshot: dict[str, tuple[Transition, ...]] = {}
    for name in MODE_NAMES:
        raw = modes[name]
        if isinstance(raw, (str, bytes, bytearray)):
            raise InvalidModePackage(f'{name} mode must be an iterable of transitions')
        try:
            edges = tuple(raw)
        except TypeError as exc:
            raise InvalidModePackage(f'{name} mode must be an iterable of transitions') from exc
        if any(not isinstance(edge, Transition) for edge in edges):
            raise InvalidModePackage(f'{name} mode edges must be Transition values')
        snapshot[name] = edges
    return snapshot


def _aggregate(protocol: Protocol) -> dict[tuple[int, int], Fraction]:
    index = {name: position for position, name in enumerate(protocol.states)}
    totals: dict[tuple[int, int], Fraction] = {}
    for edge in protocol.transitions:
        key = (index[edge.source], index[edge.destination])
        totals[key] = totals.get(key, ZERO) + edge.probability
    return totals


def _canonical_rows(
    totals: dict[tuple[int, int], Fraction], live: tuple[int, ...],
    pass_index: int, fail_index: int,
) -> dict[str, list]:
    continuation: list[list] = []
    passes: list[list[str]] = []
    failures: list[list[str]] = []
    for source in live:
        row: list[tuple[int, Fraction]] = []
        direct_pass = ZERO
        direct_fail = ZERO
        for (origin, destination), weight in totals.items():
            if origin != source or not weight:
                continue
            if destination == pass_index:
                direct_pass += weight
            elif destination == fail_index:
                direct_fail += weight
            else:
                row.append((destination, weight))
        row.sort(key=lambda item: item[0])
        continuation.append([[target, encode_fraction(weight)] for target, weight in row])
        passes.append(encode_fraction(direct_pass))
        failures.append(encode_fraction(direct_fail))
    return {'continuation': continuation, 'pass': passes, 'fail': failures}


def issue_mode(store: RevisionStore, revision_id: str, modes: Mapping[str, object]) -> Package:
    """Issue a canonical V2 package for a fixed revision and a two-mode table."""
    revision = store.get(revision_id)
    if revision is None:
        raise KeyError(revision_id)
    protocol = revision.protocol
    snapshot = _snapshot_modes(modes)

    aggregates: dict[str, dict[tuple[int, int], Fraction]] = {}
    for name in MODE_NAMES:
        candidate = Protocol(protocol.bench_id, protocol.states, protocol.start,
                             protocol.pass_state, protocol.fail_state, snapshot[name])
        validate_protocol(candidate)
        aggregates[name] = _aggregate(candidate)

    index = {name: position for position, name in enumerate(protocol.states)}
    pass_index = index[protocol.pass_state]
    fail_index = index[protocol.fail_state]
    start_index = index[protocol.start]
    live = tuple(position for position, name in enumerate(protocol.states)
                 if name not in (protocol.pass_state, protocol.fail_state))
    fixed = _aggregate(protocol)
    for source in live:
        for destination in range(len(protocol.states)):
            average = (aggregates['high'].get((source, destination), ZERO)
                       + aggregates['low'].get((source, destination), ZERO)) / 2
            if average != fixed.get((source, destination), ZERO):
                raise InvalidModePackage(
                    'the equal mode average must reproduce the fixed revision')

    payload = {
        'initial': start_index,
        'pass_terminal': pass_index,
        'fail_terminal': fail_index,
        'live': list(live),
        'modes': {name: _canonical_rows(aggregates[name], live, pass_index, fail_index)
                  for name in MODE_NAMES},
    }
    mode_digest = sha256(encode_body(payload)).hexdigest()
    analysis_id = f'A2-{revision_id}-{mode_digest}'
    package_id = f'P2-{analysis_id}'
    body = encode_body({
        **payload,
        'format': 'V2',
        'package_id': package_id,
        'analysis_id': analysis_id,
        'bench_id': protocol.bench_id,
        'revision_id': revision_id,
    })
    return Package(
        PackageIdentity(package_id, analysis_id, protocol.bench_id, revision_id,
                        sha256(body).hexdigest()),
        body,
    )


def _decode_rows(raw: object, live: tuple[int, ...]) -> ModeRows:
    if not isinstance(raw, dict) or set(raw.keys()) != _MODE_KEYS:
        raise InvalidModePackage('a mode must hold continuation, pass and fail rows')
    continuation = raw['continuation']
    passes = raw['pass']
    failures = raw['fail']
    if (not isinstance(continuation, list) or not isinstance(passes, list)
            or not isinstance(failures, list) or len(continuation) != len(live)
            or len(passes) != len(live) or len(failures) != len(live)):
        raise InvalidModePackage('mode rows must align with the live states')
    live_set = set(live)
    rows: list[tuple[tuple[int, Fraction], ...]] = []
    direct_pass: list[Fraction] = []
    direct_fail: list[Fraction] = []
    for position in range(len(live)):
        encoded_row = continuation[position]
        if not isinstance(encoded_row, list):
            raise InvalidModePackage('a continuation row must be a list')
        parsed: list[tuple[int, Fraction]] = []
        total = ZERO
        previous = -1
        for edge in encoded_row:
            if not isinstance(edge, list) or len(edge) != 2:
                raise InvalidModePackage('a continuation edge must be a pair')
            target, encoded_weight = edge
            if type(target) is not int or target not in live_set or target <= previous:
                raise InvalidModePackage('continuation targets must be ordered live indices')
            try:
                weight = decode_fraction(encoded_weight)
            except PackageFormatError as exc:
                raise InvalidModePackage('invalid continuation weight') from exc
            if weight <= 0:
                raise InvalidModePackage('continuation weights must be positive')
            parsed.append((target, weight))
            total += weight
            previous = target
        try:
            passed = decode_fraction(passes[position])
            failed = decode_fraction(failures[position])
        except PackageFormatError as exc:
            raise InvalidModePackage('invalid terminal mass') from exc
        if not 0 <= passed <= 1 or not 0 <= failed <= 1:
            raise InvalidModePackage('terminal mass must lie in [0, 1]')
        if total + passed + failed != 1:
            raise InvalidModePackage('each anonymous row must conserve probability')
        rows.append(tuple(parsed))
        direct_pass.append(passed)
        direct_fail.append(failed)
    return ModeRows(tuple(rows), tuple(direct_pass), tuple(direct_fail))


def decode_mode_body(raw: bytes) -> ModeBody:
    """Validate and read a canonical V2 body without any station state."""
    try:
        body = decode_body(raw)
    except PackageFormatError as exc:
        raise InvalidModePackage('invalid mode package body') from exc
    if not isinstance(body, dict) or set(body.keys()) != _BODY_KEYS:
        raise InvalidModePackage('not a V2 mode package body')
    if body.get('format') != 'V2':
        raise InvalidModePackage('not a V2 mode package body')
    identifiers = {name: body.get(name) for name in
                   ('package_id', 'analysis_id', 'bench_id', 'revision_id')}
    if any(not _is_identifier(value) for value in identifiers.values()):
        raise InvalidModePackage('package identifiers must be nonempty text')
    for coded in ('package_id', 'analysis_id'):
        if not _is_ascii(identifiers[coded]):
            raise InvalidModePackage('coded identifiers must be ASCII text')

    initial = _int(body.get('initial'), 'initial')
    pass_terminal = _int(body.get('pass_terminal'), 'pass_terminal')
    fail_terminal = _int(body.get('fail_terminal'), 'fail_terminal')
    raw_live = body.get('live')
    if not isinstance(raw_live, list) or not raw_live:
        raise InvalidModePackage('live must be a nonempty list')
    live = tuple(_int(value, 'live index') for value in raw_live)
    if any(live[position] >= live[position + 1] for position in range(len(live) - 1)):
        raise InvalidModePackage('live indices must be strictly increasing')
    if initial not in live:
        raise InvalidModePackage('the initial state must be live')
    if pass_terminal == fail_terminal:
        raise InvalidModePackage('the two terminals must be distinct')
    if pass_terminal in live or fail_terminal in live:
        raise InvalidModePackage('terminal roles must not be live states')

    raw_modes = body.get('modes')
    if not isinstance(raw_modes, dict) or set(raw_modes.keys()) != set(MODE_NAMES):
        raise InvalidModePackage('modes must have exactly the high and low keys')
    high = _decode_rows(raw_modes['high'], live)
    low = _decode_rows(raw_modes['low'], live)
    evidence = ModeEvidence(initial, pass_terminal, fail_terminal, live, high, low)
    return ModeBody(identifiers['package_id'], identifiers['analysis_id'],
                    identifiers['bench_id'], identifiers['revision_id'], evidence)
