"""V2 shared-mode report drafts, archive publication and read-back."""
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from records.connection import archive_transaction
from records.receipt import Receipt, decode_receipt, encode_receipt, make_receipt
from records.schema import ParentReference, decode_probability, encode_probability


@dataclass(frozen=True, slots=True)
class ModeReportDraft:
    horizon: int
    lower: Fraction
    upper: Fraction
    parents: tuple[ParentReference, ParentReference]

    def __post_init__(self) -> None:
        if type(self.horizon) is not int or self.horizon < 1:
            raise ValueError('a mode report needs a positive integer horizon')
        for name, value in (('lower', self.lower), ('upper', self.upper)):
            if not isinstance(value, Fraction):
                raise ValueError(f'{name} bound must be an exact fraction')
            if not 0 <= value <= 1:
                raise ValueError(f'{name} bound must lie in [0, 1]')
        if self.lower > self.upper:
            raise ValueError('the lower bound must not exceed the upper bound')
        if not isinstance(self.parents, tuple) or len(self.parents) != 2:
            raise ValueError('a mode report must have two ordered parents')
        left, right = self.parents
        if left.bench_id == right.bench_id or left.accepted_id == right.accepted_id:
            raise ValueError('a mode report must refer to two distinct bench acceptances')


@dataclass(frozen=True, slots=True)
class PublishedModeReport:
    report_id: str
    draft: ModeReportDraft
    receipt: Receipt


_READ_MODE_REPORT = '''
SELECT r.horizon, r.lower_n, r.lower_d, r.upper_n, r.upper_d, receipt.body,
       lhs.accepted_id, lhs.package_id, lhs.bench_id, lhs.revision_id, lhs.analysis_id, lhs.digest,
       rhs.accepted_id, rhs.package_id, rhs.bench_id, rhs.revision_id, rhs.analysis_id, rhs.digest
FROM mode_receipts AS receipt
JOIN mode_reports AS r ON r.id = receipt.report_id
JOIN mode_report_parents AS lhs ON lhs.report_id = r.id AND lhs.slot = 0
JOIN mode_report_parents AS rhs ON rhs.report_id = r.id AND rhs.slot = 1
WHERE receipt.report_number = ?
'''


def publish_mode(
    connection: sqlite3.Connection,
    draft: ModeReportDraft,
    *,
    before_commit: Callable[[], None] | None = None,
) -> PublishedModeReport:
    lower = encode_probability(draft.lower)
    upper = encode_probability(draft.upper)
    left, right = draft.parents
    with archive_transaction(connection):
        cursor = connection.execute(
            '''INSERT INTO mode_reports (horizon, lower_n, lower_d, upper_n, upper_d)
               VALUES (?, ?, ?, ?, ?)''',
            (draft.horizon, lower[0], lower[1], upper[0], upper[1]),
        )
        number = cursor.lastrowid
        report_id = f'SM-{number:08d}'
        connection.executemany(
            '''INSERT INTO mode_report_parents
               (report_id, slot, accepted_id, package_id, bench_id, revision_id, analysis_id, digest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            [(number, slot, parent.accepted_id, parent.package_id, parent.bench_id,
              parent.revision_id, parent.analysis_id, parent.digest)
             for slot, parent in enumerate(draft.parents)],
        )
        receipt = make_receipt(report_id, (left.accepted_id, right.accepted_id))
        connection.execute(
            'INSERT INTO mode_receipts (report_id, report_number, body) VALUES (?, ?, ?)',
            (number, report_id, encode_receipt(receipt)),
        )
        if before_commit is not None:
            before_commit()
    return PublishedModeReport(report_id, draft, receipt)


def get_mode(connection: sqlite3.Connection, report_id: str) -> PublishedModeReport | None:
    """Read a signed V2 report with only the archive open."""
    row = connection.execute(_READ_MODE_REPORT, (report_id,)).fetchone()
    if row is None:
        return None
    lower = decode_probability(row[1], row[2])
    upper = decode_probability(row[3], row[4])
    parents = ParentReference(*row[6:12]), ParentReference(*row[12:18])
    draft = ModeReportDraft(row[0], lower, upper, parents)
    return PublishedModeReport(report_id, draft, decode_receipt(row[5]))
