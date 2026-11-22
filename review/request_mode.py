import sqlite3
from collections.abc import Callable

from intake.catalog import IntakeCatalog
from intake.modes import decode_mode_body
from intake.select import ParentSelectionError, select_pair
from records.mode import PublishedModeReport, publish_mode
from review.report_mode import build_mode_report


def sign_mode(
    catalog: IntakeCatalog,
    archive: sqlite3.Connection,
    left_id: str,
    right_id: str,
    horizon: int,
    *,
    before_commit: Callable[[], None] | None = None,
) -> PublishedModeReport:
    if type(horizon) is not int or horizon < 1:
        raise ValueError('horizon must be a positive integer')
    pair = select_pair(catalog, left_id, right_id)
    for label, accepted in (('left', pair.left), ('right', pair.right)):
        try:
            decode_mode_body(accepted.package.body)
        except ValueError as exc:
            raise ParentSelectionError(
                f'{label} acceptance is not a V2 mode delivery') from exc
    draft = build_mode_report(pair, horizon)
    return publish_mode(archive, draft, before_commit=before_commit)
