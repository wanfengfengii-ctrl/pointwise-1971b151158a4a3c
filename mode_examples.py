"""Supply-pressure inputs and a runnable comparison with the existing report."""
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction

from analysis import freeze
from intake import IntakeCatalog, accept, pack
from protocol import RevisionStore, Transition, revise
from records import connect_archive, get, initialize_archive
from review import sign


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
RETRY_BENCH = BenchInput(TERMINALS, edges(("start", "passed", HALF), ("start", "start", HALF)),
                         PASS, edges(("start", "start", 1)))

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


if __name__ == "__main__":
    for example in EXAMPLES:
        show_legacy(example)
