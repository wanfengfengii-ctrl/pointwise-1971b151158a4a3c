"""Two-mode supply inputs, the V1 comparison and runnable V2 reports.

Run from the repository root with ``python3 -B mode_examples.py``.  Each
example registers its benches on a station, freezes the V1 revision, then
issues a V2 anonymous two-mode package for the same fixed revision.  The
night shift accepts the deliveries, signs at every listed horizon and reads
the stored conclusions back with only the archive reopened.
"""
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
from tempfile import TemporaryDirectory
from pathlib import Path

from analysis import freeze
from intake import IntakeCatalog, accept, accept_mode, issue_mode, pack
from protocol import RevisionStore, Transition, revise
from records import connect_archive, get, get_mode, initialize_archive
from review import sign, sign_mode


ZERO = Fraction(0)
HALF = Fraction(1, 2)
QUARTER = Fraction(1, 4)


def edges(*rows):
    return tuple(Transition(source, target, Fraction(weight)) for source, target, weight in rows)


@dataclass(frozen=True)
class BenchInput:
    states: tuple[str, ...]
    mean: tuple[Transition, ...]
    high: tuple[Transition, ...]
    low: tuple[Transition, ...]

    def register(self, station, bench_id):
        return revise(station, bench_id, states=self.states, start="start",
                      pass_state="passed", fail_state="failed", transitions=self.mean)

    def modes(self):
        return {"high": self.high, "low": self.low}


@dataclass(frozen=True)
class Example:
    name: str
    left: BenchInput
    right: BenchInput
    bounds: tuple[tuple[int, Fraction, Fraction], ...]
    legacy_sync: Fraction = QUARTER


TERMINALS = ("start", "passed", "failed")
PASS = edges(("start", "passed", 1))
FAIL = edges(("start", "failed", 1))
SINGLE_MEAN = edges(("start", "passed", HALF), ("start", "failed", HALF))
HIGH_ONLY = BenchInput(TERMINALS, SINGLE_MEAN, PASS, FAIL)
LOW_ONLY = BenchInput(TERMINALS, SINGLE_MEAN, FAIL, PASS)

SPLIT = edges(("start", "A", HALF), ("start", "B", HALF),
              ("A", "passed", 1), ("B", "failed", 1))
REACHED = edges(("start", "R", 1), ("R", "passed", HALF), ("R", "failed", HALF))
SPLIT_BENCH = BenchInput(TERMINALS + ("A", "B"), SPLIT, SPLIT, SPLIT)
REACHED_BENCH = BenchInput(TERMINALS + ("R",), REACHED, REACHED, REACHED)

WAIT_ROW = edges(("start", "passed", HALF), ("start", "wait", HALF), ("wait", "failed", 1))
FREE_BENCH = BenchInput(TERMINALS + ("wait",), WAIT_ROW, WAIT_ROW, WAIT_ROW)
RETRY_MEAN = edges(("start", "passed", HALF), ("start", "start", HALF))
CONTINUE = edges(("start", "start", 1))
RETRY_BENCH = BenchInput(TERMINALS, RETRY_MEAN, PASS, CONTINUE)

EXAMPLES = (
    Example("same_mode", HIGH_ONLY, HIGH_ONLY, ((1, HALF, HALF),)),
    Example("opposite_modes", HIGH_ONLY, LOW_ONLY, ((1, ZERO, ZERO),)),
    Example("conditioned_rows", SPLIT_BENCH, REACHED_BENCH,
            ((1, ZERO, ZERO), (2, QUARTER, QUARTER))),
    Example("free_joint", FREE_BENCH, FREE_BENCH, ((1, ZERO, HALF),)),
    Example("continuation_choice", SPLIT_BENCH, SPLIT_BENCH,
            ((1, ZERO, ZERO), (2, ZERO, HALF))),
    Example("fresh_mode", RETRY_BENCH, RETRY_BENCH,
            ((1, HALF, HALF), (2, Fraction(3, 4), Fraction(3, 4))), Fraction(1, 3)),
)


def issue(definition, station, intake, bench_id):
    """Register, freeze V1, issue V2 and accept it; return ids and evidence."""
    revision = definition.register(station, bench_id)
    _, frozen = freeze(station, revision)
    legacy = pack(frozen)
    mode_package = issue_mode(station, revision, definition.modes())
    legacy_id = accept(intake, legacy, legacy.identity)
    mode_id = accept_mode(intake, mode_package, mode_package.identity)
    return revision, legacy_id, mode_id


def show_legacy(example):
    with closing(RevisionStore(":memory:")) as station:
        station.initialize()
        packages = []
        for bench_id, definition in (("左台", example.left), ("右台", example.right)):
            _, frozen = freeze(station, definition.register(station, bench_id))
            packages.append(pack(frozen))
    with (closing(IntakeCatalog(":memory:")) as intake,
          closing(connect_archive(":memory:")) as archive):
        intake.initialize()
        initialize_archive(archive)
        left, right = (accept(intake, item, item.identity) for item in packages)
        report = sign(intake, archive, left, right)
        if report.draft.synchronous != example.legacy_sync or get(archive, report.report_id) != report:
            raise AssertionError(f"{example.name}: V1 calculation or archive read differs")
        print(f"{example.name}: V1 同拍联合质量 {report.draft.synchronous}")


def run_example(directory, example):
    station_path = directory / f"{example.name}.station.sqlite"
    intake_path = directory / f"{example.name}.intake.sqlite"
    archive_path = directory / f"{example.name}.archive.sqlite"
    signed = []
    with closing(RevisionStore(station_path)) as station:
        station.initialize()
        intake = IntakeCatalog(intake_path)
        intake.initialize()
        left_revision, _, left_mode = issue(example.left, station, intake, "左台")
        right_revision, _, right_mode = issue(example.right, station, intake, "右台")
        station.close()
        with closing(connect_archive(archive_path)) as archive:
            initialize_archive(archive)
            for horizon, lower, upper in example.bounds:
                report = sign_mode(intake, archive, left_mode, right_mode, horizon)
                if (report.draft.lower, report.draft.upper) != (lower, upper):
                    raise AssertionError(
                        f"{example.name} H={horizon}: expected [{lower},{upper}] "
                        f"got [{report.draft.lower},{report.draft.upper}]")
                signed.append(report)
            intake.close()
    # Only the archive is reopened; every stored conclusion must come back exactly.
    with closing(connect_archive(archive_path)) as archive:
        initialize_archive(archive)
        for report in signed:
            if get_mode(archive, report.report_id) != report:
                raise AssertionError(f"{example.name}: archive read differs")
            print(f"{example.name} {report.report_id} 截止 {report.draft.horizon} "
                  f"下界 {report.draft.lower} 上界 {report.draft.upper} "
                  f"首执 {report.receipt.receipt_id}")
    if any(report.draft.parents[0].revision_id != left_revision
           or report.draft.parents[1].revision_id != right_revision for report in signed):
        raise AssertionError(f"{example.name}: parent revisions were not preserved")


def show_mode_switch(directory):
    """One fixed right revision can issue internally different mode tables."""
    station_path = directory / "switch.station.sqlite"
    intake_path = directory / "switch.intake.sqlite"
    archive_path = directory / "switch.archive.sqlite"
    with closing(RevisionStore(station_path)) as station:
        station.initialize()
        left_high = HIGH_ONLY.register(station, "左台")
        right_revision = HIGH_ONLY.register(station, "右台")
        intake = IntakeCatalog(intake_path)
        intake.initialize()
        left_package = issue_mode(station, left_high, HIGH_ONLY.modes())
        high_package = issue_mode(station, right_revision, HIGH_ONLY.modes())
        low_package = issue_mode(station, right_revision, LOW_ONLY.modes())
        if high_package.identity == low_package.identity:
            raise AssertionError("different mode semantics must issue different identities")
        left_id = accept_mode(intake, left_package, left_package.identity)
        high_id = accept_mode(intake, high_package, high_package.identity)
        low_id = accept_mode(intake, low_package, low_package.identity)
        station.close()
        with closing(connect_archive(archive_path)) as archive:
            initialize_archive(archive)
            same = sign_mode(intake, archive, left_id, high_id, 1)
            opposite = sign_mode(intake, archive, left_id, low_id, 1)
            intake.close()
    with closing(connect_archive(archive_path)) as archive:
        same = get_mode(archive, same.report_id)
        opposite = get_mode(archive, opposite.report_id)
    if (same.draft.lower, same.draft.upper) != (HALF, HALF):
        raise AssertionError("same-mode switch signed unexpected bounds")
    if (opposite.draft.lower, opposite.draft.upper) != (ZERO, ZERO):
        raise AssertionError("opposite-mode switch signed unexpected bounds")
    print(f"mode_switch {same.report_id} 同档 [{same.draft.lower},{same.draft.upper}] "
          f"{opposite.report_id} 反档 [{opposite.draft.lower},{opposite.draft.upper}]")


def main():
    for example in EXAMPLES:
        show_legacy(example)
    with TemporaryDirectory(prefix="mode-review-") as temporary:
        directory = Path(temporary)
        for example in EXAMPLES:
            run_example(directory, example)
        show_mode_switch(directory)


if __name__ == "__main__":
    main()
