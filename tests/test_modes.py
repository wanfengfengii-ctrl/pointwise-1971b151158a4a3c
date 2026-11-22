from dataclasses import replace
from fractions import Fraction
from hashlib import sha256

from analysis import freeze
from intake import accept, accept_mode, issue_mode, pack
from intake.admit import InvalidPackage
from intake.catalog import PackageConflict
from intake.codec import decode_body, encode_body
from mode_examples import EXAMPLES
from records import get, get_mode
from review import sign, sign_mode
from review.bounds import reachable_bounds
from protocol import Transition
from tests import HALF, ONE, ZERO, LabCase


def repack(original, body):
    data = encode_body(body)
    return replace(original, body=data,
                   identity=replace(original.identity, digest=sha256(data).hexdigest()))


class _ModeBase(LabCase):
    def prepared(self, example):
        station = self.open_station()
        intake = self.open_intake()
        packages = []
        for bench_id, definition in (("左台", example.left), ("右台", example.right)):
            revision = definition.register(station, bench_id)
            _, frozen = freeze(station, revision)
            legacy = pack(frozen)
            delivery = issue_mode(station, revision, definition.modes())
            packages.append((revision, accept(intake, legacy, legacy.identity),
                             accept_mode(intake, delivery, delivery.identity), delivery))
        station.close()
        return intake, packages


class ModeBoundsTests(_ModeBase):
    def test_example_bounds(self):
        for example in EXAMPLES:
            with self.subTest(example=example.name):
                intake, packages = self.prepared(example)
                archive = self.open_archive()
                ids = [packages[0][2], packages[1][2]]
                for horizon, lower, upper in example.bounds:
                    signed = sign_mode(intake, archive, ids[0], ids[1], horizon)
                    self.assertEqual((signed.draft.horizon, signed.draft.lower, signed.draft.upper),
                                     (horizon, lower, upper))
                    self.assertLessEqual(signed.draft.lower, signed.draft.upper)
                archive.close()
                intake.close()

    def test_legacy_report_still_computed_from_same_revision(self):
        for example in EXAMPLES:
            with self.subTest(example=example.name):
                intake, packages = self.prepared(example)
                archive = self.open_archive()
                legacy = sign(intake, archive, packages[0][1], packages[1][1])
                self.assertEqual(legacy.draft.synchronous, example.legacy_sync)
                archive.close()
                intake.close()

    def test_wide_exact_bounds_survive_reopen(self):
        station = self.open_station()
        intake = self.open_intake()
        chance = Fraction(1, 2**80 + 3)
        transition = (Transition('试', '过', chance), Transition('试', '败', 1 - chance))
        revision = self.root_revision(station, transition)
        delivery = issue_mode(station, revision, {'high': transition, 'low': transition})
        left = accept_mode(intake, delivery, delivery.identity)
        other = self.root_revision(station, transition, '右台')
        second = issue_mode(station, other, {'high': transition, 'low': transition})
        right = accept_mode(intake, second, second.identity)
        station.close()
        archive = self.open_archive()
        signed = sign_mode(intake, archive, left, right, 1)
        # Identical modes still allow an anti-correlated coupling within the row marginals.
        self.assertEqual((signed.draft.lower, signed.draft.upper), (ZERO, chance))
        archive.close()
        intake.close()
        reopened = self.open_archive()
        self.assertEqual(get_mode(reopened, signed.report_id), signed)

    def root_revision(self, station, transitions, bench='左台'):
        from protocol import revise
        return revise(station, bench, states=('试', '过', '败'), start='试',
                      pass_state='过', fail_state='败', transitions=transitions)

    def test_bounds_are_monotone_in_horizon(self):
        from mode_examples import FREE_BENCH
        station = self.open_station()
        left = FREE_BENCH.register(station, '左台')
        right = FREE_BENCH.register(station, '右台')
        left_package = issue_mode(station, left, FREE_BENCH.modes())
        right_package = issue_mode(station, right, FREE_BENCH.modes())
        from intake.modes import decode_mode_body
        evidence = (decode_mode_body(left_package.body).evidence,
                    decode_mode_body(right_package.body).evidence)
        station.close()
        previous = (ZERO, ZERO)
        for horizon in (1, 2, 3, 5):
            current = reachable_bounds(evidence[0], evidence[1], horizon)
            self.assertLessEqual(current[0], current[1])
            self.assertLessEqual(previous[0], current[0])
            self.assertLessEqual(previous[1], current[1])
            previous = current

    def test_bounds_reject_nonpositive_horizon(self):
        from mode_examples import HIGH_ONLY
        station = self.open_station()
        revision = HIGH_ONLY.register(station, '左台')
        package = issue_mode(station, revision, HIGH_ONLY.modes())
        from intake.modes import decode_mode_body
        evidence = decode_mode_body(package.body).evidence
        station.close()
        for horizon in (0, -3, True, 2.0):
            with self.assertRaises(ValueError):
                reachable_bounds(evidence, evidence, horizon)


class ModeIssueTests(_ModeBase):
    def test_issue_rejects_unknown_and_bad_tables(self):
        station = self.open_station()
        revision = self._revision(station)
        with self.assertRaises(KeyError):
            issue_mode(station, 'R-00000099', {'high': (), 'low': ()})
        valid = (Transition('start', 'passed', ONE),)
        cases = (
            {'high': valid},
            {'high': valid, 'low': valid, 'medium': valid},
            {'high': valid, 'low': (Transition('start', 'passed', Fraction(1, 2)),)},
            {'high': 'not a mode', 'low': valid},
            {'high': (object(),), 'low': valid},
            {'high': (Transition('start', 'missing', ONE),), 'low': valid},
        )
        for modes in cases:
            with self.subTest(modes=modes):
                with self.assertRaises(ValueError):
                    issue_mode(station, revision, modes)
        with self.assertRaises(ValueError):
            issue_mode(station, revision, None)

    def test_issue_checks_average_including_unreached_rows(self):
        station = self.open_station()
        revision = self._split_revision(station)
        unfair_high = (
            Transition('start', 'passed', Fraction(3, 4)),
            Transition('start', 'failed', Fraction(1, 4)),
        )
        with self.assertRaises(ValueError):
            issue_mode(station, revision, {'high': unfair_high, 'low': self._split()})

    def test_issue_checks_conditioned_right_row(self):
        station = self.open_station()
        from mode_examples import REACHED
        revision = self._revision(station, REACHED, states=('start', 'passed', 'failed', 'R'))
        altered = (Transition('start', 'R', ONE), Transition('R', 'passed', Fraction(1, 4)),
                   Transition('R', 'failed', Fraction(3, 4)))
        with self.assertRaises(ValueError):
            issue_mode(station, revision, {'high': altered, 'low': REACHED})

    def test_identity_stability_and_semantic_distinction(self):
        station = self.open_station()
        revision = self._revision(station)
        pass_split = (Transition('start', 'passed', Fraction(1, 4)),
                      Transition('start', 'passed', Fraction(3, 4)))
        direct = (Transition('start', 'passed', ONE),)
        fail = (Transition('start', 'failed', ONE),)
        first = issue_mode(station, revision, {'high': pass_split, 'low': fail})
        again = issue_mode(station, revision, {'high': direct, 'low': fail})
        self.assertEqual(first.identity, again.identity)
        self.assertEqual(first.body, again.body)
        opposite = issue_mode(station, revision, {'high': fail, 'low': direct})
        self.assertNotEqual(first.identity, opposite.identity)
        self.assertEqual(first.identity.revision_id, opposite.identity.revision_id)
        self.assertEqual(first.identity.revision_id, revision)

    def test_edge_order_and_duplication_do_not_change_bytes(self):
        station = self.open_station()
        forward = (Transition('start', 'passed', Fraction(1, 3)),
                   Transition('start', 'failed', Fraction(2, 3)))
        revision = self._revision(station, forward)
        shuffled = (Transition('start', 'failed', Fraction(1, 6)),
                    Transition('start', 'passed', Fraction(1, 6)),
                    Transition('start', 'failed', Fraction(1, 2)),
                    Transition('start', 'passed', Fraction(1, 6)),
                    Transition('start', 'start', Fraction(0)))
        first = issue_mode(station, revision, {'high': forward, 'low': forward})
        second = issue_mode(station, revision, {'low': shuffled, 'high': shuffled})
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.identity, second.identity)

    def test_unreachable_rows_still_checked(self):
        station = self.open_station()
        states = ('start', 'passed', 'failed', 'A', 'B', 'U')
        edges = (Transition('start', 'A', HALF), Transition('start', 'B', HALF),
                 Transition('A', 'passed', ONE), Transition('B', 'failed', ONE),
                 Transition('U', 'passed', ONE))
        revision = self._revision(station, edges, states=states)
        skew = (Transition('start', 'A', HALF), Transition('start', 'B', HALF),
                Transition('A', 'passed', ONE), Transition('B', 'failed', ONE),
                Transition('U', 'failed', ONE))
        with self.assertRaises(ValueError):
            issue_mode(station, revision, {'high': skew, 'low': edges})

    def test_issue_does_not_touch_the_station_table(self):
        station = self.open_station()
        revision = self._revision(station)
        before = station.get(revision)
        current = station.current('左台')
        issue_mode(station, revision, {'high': (Transition('start', 'passed', ONE),),
                                       'low': (Transition('start', 'failed', ONE),)})
        self.assertEqual(station.get(revision), before)
        self.assertEqual(station.current('左台'), current)

    def test_issue_is_anonymous(self):
        station = self.open_station()
        revision = self._split_revision(station)
        package = issue_mode(station, revision, {'high': self._split(), 'low': self._split()})
        self.assertNotIn('内部台架', str(package.body))
        self.assertEqual(decode_body(package.body)['format'], 'V2')

    def _revision(self, station, transitions=None, bench='左台', states=('start', 'passed', 'failed')):
        from protocol import revise
        return revise(station, bench, states=states, start='start', pass_state='passed',
                      fail_state='failed', transitions=transitions or
                      (Transition('start', 'passed', HALF), Transition('start', 'failed', HALF)))

    def _split(self):
        return (Transition('start', 'A', HALF), Transition('start', 'B', HALF),
                Transition('A', 'passed', ONE), Transition('B', 'failed', ONE))

    def _split_revision(self, station):
        return self._revision(station, self._split(),
                              states=('start', 'passed', 'failed', 'A', 'B'))


class ModeAcceptTests(_ModeBase):
    def setUp(self):
        super().setUp()
        example = EXAMPLES[0]
        station = self.open_station()
        revision = example.left.register(station, '左台')
        _, frozen = freeze(station, revision)
        self.legacy = pack(frozen)
        self.package = issue_mode(station, revision, example.left.modes())
        station.close()

    def test_duplicate_v2_reuses_acceptance(self):
        intake = self.open_intake()
        accepted = accept_mode(intake, self.package, self.package.identity)
        self.assertEqual(accept_mode(intake, self.package, self.package.identity), accepted)
        self.assertEqual(intake.get(accepted).package, self.package)

    def test_rejects_wrong_binding_bad_body_and_v1(self):
        intake = self.open_intake()
        body = decode_body(self.package.body)
        wrong = replace(self.package.identity, analysis_id='A2-other')
        cases = (
            (self.package, wrong),
            (replace(self.package, identity=wrong), wrong),
            (self.legacy, self.legacy.identity),
            (replace(self.package, body=self.package.body + b' '), self.package.identity),
            (self._mutated(body, {'format': 'V1'}), None),
            (self._mutated(body, {'modes': {'high': body['modes']['high']}}), None),
        )
        for package, expected in cases:
            with self.subTest(case=package.identity.package_id):
                with self.assertRaises(ValueError):
                    accept_mode(intake, package, expected or package.identity)
        self.assertIsNone(intake.get(f'I-{self.package.identity.package_id}'))

    def test_v2_never_signed_through_the_v1_path(self):
        station = self.open_station()
        intake = self.open_intake()
        revision = EXAMPLES[0].left.register(station, '左台')
        other = EXAMPLES[0].left.register(station, '右台')
        left_package = issue_mode(station, revision, EXAMPLES[0].left.modes())
        right_package = issue_mode(station, other, EXAMPLES[0].left.modes())
        left = accept_mode(intake, left_package, left_package.identity)
        right = accept_mode(intake, right_package, right_package.identity)
        station.close()
        archive = self.open_archive()
        with self.assertRaises(ValueError):
            sign(intake, archive, left, right)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM reports').fetchone()[0], 0)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 0)

    def test_v1_accept_refuses_v2_with_invalid_package(self):
        intake = self.open_intake()
        with self.assertRaises(InvalidPackage):
            accept(intake, self.package, self.package.identity)
        self.assertIsNone(intake.get(f'I-{self.package.identity.package_id}'))

    def test_same_number_other_body_conflicts(self):
        intake = self.open_intake()
        accept_mode(intake, self.package, self.package.identity)
        body = decode_body(self.package.body)
        # Same package number, still a valid V2 table, but the modes are swapped.
        body['modes']['high'], body['modes']['low'] = body['modes']['low'], body['modes']['high']
        alternate = repack(self.package, body)
        with self.assertRaises(PackageConflict):
            accept_mode(intake, alternate, alternate.identity)

    def test_rows_must_conserve_mass(self):
        intake = self.open_intake()
        body = decode_body(self.package.body)
        body['modes']['low']['fail'][0] = ['1', '2']
        broken = repack(self.package, body)
        with self.assertRaises(ValueError):
            accept_mode(intake, broken, broken.identity)

    def _mutated(self, body, changes):
        clone = {**body, **changes}
        return repack(self.package, clone)


class ModeSignTests(_ModeBase):
    def pair(self, left_definition, right_definition, horizon=1):
        station = self.open_station()
        intake = self.open_intake()
        ids = []
        for bench_id, definition in (('左台', left_definition), ('右台', right_definition)):
            revision = definition.register(station, bench_id)
            _, frozen = freeze(station, revision)
            legacy = pack(frozen)
            delivery = issue_mode(station, revision, definition.modes())
            ids.append((accept(intake, legacy, legacy.identity),
                        accept_mode(intake, delivery, delivery.identity)))
        station.close()
        return intake, ids

    def test_offline_sign_and_reopen(self):
        from mode_examples import HIGH_ONLY
        intake, ids = self.pair(HIGH_ONLY, HIGH_ONLY)
        archive = self.open_archive()
        signed = sign_mode(intake, archive, ids[0][1], ids[1][1], 1)
        archive.close()
        intake.close()
        reopened = self.open_archive()
        self.assertEqual(get_mode(reopened, signed.report_id), signed)
        self.assertIsNone(get_mode(reopened, 'SM-00000099'))
        self.assertEqual(get_mode(reopened, signed.report_id).draft.lower, HALF)

    def test_mixed_version_same_bench_and_missing(self):
        from mode_examples import HIGH_ONLY, LOW_ONLY
        intake, ids = self.pair(HIGH_ONLY, LOW_ONLY)
        archive = self.open_archive()
        v1_left, v2_left = ids[0]
        _, v2_right = ids[1]
        cases = (
            (v2_left, v2_left),
            (v2_left, 'I-缺件'),
            ('I-缺件', v2_right),
            (v1_left, v2_right),
            (v2_left, v1_left),
        )
        for left, right in cases:
            with self.subTest(left=left, right=right):
                with self.assertRaises(ValueError):
                    sign_mode(intake, archive, left, right, 1)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_reports').fetchone()[0], 0)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_receipts').fetchone()[0], 0)

    def test_parent_order_follows_arguments(self):
        from mode_examples import HIGH_ONLY
        intake, ids = self.pair(HIGH_ONLY, HIGH_ONLY)
        archive = self.open_archive()
        forward = sign_mode(intake, archive, ids[0][1], ids[1][1], 1)
        reverse = sign_mode(intake, archive, ids[1][1], ids[0][1], 1)
        self.assertEqual(tuple(parent.accepted_id for parent in forward.draft.parents),
                         (ids[0][1], ids[1][1]))
        self.assertEqual(tuple(parent.accepted_id for parent in reverse.draft.parents),
                         (ids[1][1], ids[0][1]))
        self.assertEqual(forward.receipt.accepted_ids, (ids[0][1], ids[1][1]))
        self.assertEqual(reverse.receipt.accepted_ids, (ids[1][1], ids[0][1]))
        self.assertEqual(get_mode(archive, reverse.report_id).receipt.accepted_ids,
                         (ids[1][1], ids[0][1]))

    def test_revision_change_keeps_fixed_v2_report(self):
        from mode_examples import HIGH_ONLY, LOW_ONLY
        station = self.open_station()
        intake = self.open_intake()
        left_revision = HIGH_ONLY.register(station, '左台')
        right_revision = HIGH_ONLY.register(station, '右台')
        left_package = issue_mode(station, left_revision, HIGH_ONLY.modes())
        right_package = issue_mode(station, right_revision, HIGH_ONLY.modes())
        left = accept_mode(intake, left_package, left_package.identity)
        right = accept_mode(intake, right_package, right_package.identity)
        archive = self.open_archive()
        signed = sign_mode(intake, archive, left, right, 1)
        # A legal correction appends a new current revision but the fixed one stays.
        new_revision = LOW_ONLY.register(station, '左台')
        self.assertNotEqual(new_revision, left_revision)
        self.assertEqual(station.current('左台'), new_revision)
        self.assertIsNotNone(station.get(left_revision))
        reissued = issue_mode(station, left_revision, HIGH_ONLY.modes())
        self.assertEqual(reissued.identity, left_package.identity)
        self.assertEqual(reissued.body, left_package.body)
        station.close()
        intake.close()
        archive.close()
        reopened = self.open_archive()
        self.assertEqual(get_mode(reopened, signed.report_id), signed)
        self.assertEqual(signed.draft.parents[0].revision_id, left_revision)

    def test_horizon_validation(self):
        from mode_examples import HIGH_ONLY
        intake, ids = self.pair(HIGH_ONLY, HIGH_ONLY)
        archive = self.open_archive()
        for horizon in (0, -1, True, 1.0, '1', None):
            with self.subTest(horizon=horizon):
                with self.assertRaises(ValueError):
                    sign_mode(intake, archive, ids[0][1], ids[1][1], horizon)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_reports').fetchone()[0], 0)

    def test_failed_commit_then_retry(self):
        from mode_examples import HIGH_ONLY
        intake, ids = self.pair(HIGH_ONLY, HIGH_ONLY)
        archive = self.open_archive()

        def reject():
            self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_reports').fetchone()[0], 1)
            raise RuntimeError('mode commit rejected')

        with self.assertRaisesRegex(RuntimeError, 'mode commit rejected'):
            sign_mode(intake, archive, ids[0][1], ids[1][1], 1, before_commit=reject)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_reports').fetchone()[0], 0)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_report_parents').fetchone()[0], 0)
        self.assertEqual(archive.execute('SELECT COUNT(*) FROM mode_receipts').fetchone()[0], 0)
        again = sign_mode(intake, archive, ids[0][1], ids[1][1], 1)
        self.assertEqual(get_mode(archive, again.report_id), again)

    def test_v1_and_v2_reports_coexist(self):
        from mode_examples import HIGH_ONLY
        intake, ids = self.pair(HIGH_ONLY, HIGH_ONLY)
        archive = self.open_archive()
        legacy = sign(intake, archive, ids[0][0], ids[1][0])
        modern = sign_mode(intake, archive, ids[0][1], ids[1][1], 1)
        self.assertNotEqual(legacy.report_id, modern.report_id)
        self.assertEqual(get(archive, legacy.report_id), legacy)
        self.assertEqual(get_mode(archive, modern.report_id), modern)
        self.assertIsNone(get_mode(archive, legacy.report_id))
        self.assertIsNone(get(archive, modern.report_id))
        archive.close()
        intake.close()
        reopened = self.open_archive()
        self.assertEqual(get(reopened, legacy.report_id), legacy)
        self.assertEqual(get_mode(reopened, modern.report_id), modern)
