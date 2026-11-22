"""V2 shared-mode report construction from two accepted deliveries."""
from intake.catalog import AcceptedPackage
from intake.modes import ModeEvidence, decode_mode_body
from intake.select import SelectedPair
from records.mode import ModeReportDraft
from records.schema import ParentReference
from review.bounds import reachable_bounds


def _reference(accepted: AcceptedPackage) -> ParentReference:
    identity = accepted.package.identity
    return ParentReference(
        accepted.accepted_id, identity.package_id, identity.bench_id, identity.revision_id,
        identity.analysis_id, identity.digest,
    )


def _evidence(accepted: AcceptedPackage) -> ModeEvidence:
    return decode_mode_body(accepted.package.body).evidence


def build_mode_report(pair: SelectedPair, horizon: int) -> ModeReportDraft:
    if type(horizon) is not int or horizon < 1:
        raise ValueError('horizon must be a positive integer')
    lower, upper = reachable_bounds(_evidence(pair.left), _evidence(pair.right), horizon)
    return ModeReportDraft(horizon, lower, upper,
                           (_reference(pair.left), _reference(pair.right)))
