import json
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis import freeze
from intake import IntakeCatalog, accept, accept_mode, issue_mode, pack
from protocol import RevisionStore, Transition, revise
from records import connect_archive, get, get_mode, initialize_archive
from review import sign, sign_mode


def prepare(station_path, intake, bench, wait_after_miss):
    half = Fraction(1, 2)
    states = ['试验', '通过', '失败']
    edges = [Transition('试验', '通过', half)]
    if wait_after_miss:
        states.append('等待')
        edges.extend((Transition('试验', '等待', half), Transition('等待', '试验', Fraction(1))))
    else:
        edges.append(Transition('试验', '试验', half))
    with closing(RevisionStore(station_path)) as station:
        station.initialize()
        revision = revise(station, bench, states=states, start='试验', pass_state='通过',
                          fail_state='失败', transitions=edges)
        metrics, frozen = freeze(station, revision)
        package = pack(frozen)
    accepted = accept(intake, package, package.identity)
    print(f'{bench} {revision} {accepted} 首拍 {metrics.first_tick_pass} '
          f'最终通过 {metrics.eventual_pass} 永不通过 {metrics.never_pass}')
    return accepted


def check_report(case_id, signed, retrieved, expected, parents, input_data, oracle):
    if retrieved != signed or (signed.draft.joint_pass, signed.draft.conditional_sync) != (1, expected):
        raise AssertionError(f'{case_id} report calculation or retrieval mismatch')
    if tuple(parent.accepted_id for parent in retrieved.draft.parents) != parents:
        raise AssertionError(f'{case_id} report parent mismatch')
    if retrieved.receipt.accepted_ids != parents:
        raise AssertionError(f'{case_id} receipt parent mismatch')
    print(f'{retrieved.report_id} 共同通过 {retrieved.draft.joint_pass} '
          f'同拍条件 {retrieved.draft.conditional_sync} 首执 {retrieved.receipt.receipt_id}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id,
        'input': input_data,
        'expected': {'joint_pass': '1', 'conditional_sync': str(expected)},
        'actual': {'joint_pass': str(signed.draft.joint_pass),
                   'conditional_sync': str(signed.draft.conditional_sync)},
        'downstream_expected': {'parents': parents, 'conditional_sync': str(expected)},
        'downstream_actual': {'parents': [parent.accepted_id for parent in retrieved.draft.parents],
                              'conditional_sync': str(retrieved.draft.conditional_sync)},
        'code_paths': ['analysis/freeze.py', 'intake/admit.py', 'review/sync.py',
                       'records/publish.py', 'records/lookup.py'],
        'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True))


MODE_HALF = Fraction(1, 2)


def run_mode_demo(directory):
    """Same and opposite mode tables signed and read back after the station closes."""
    station_path = directory / 'mode-station.sqlite'
    archive_path = directory / 'mode-archive.sqlite'
    with (closing(IntakeCatalog(directory / 'mode-intake.sqlite')) as intake,
          closing(RevisionStore(station_path)) as station):
        intake.initialize()
        station.initialize()
        fixed = (Transition('试验', '通过', MODE_HALF), Transition('试验', '失败', MODE_HALF))
        pass_only = (Transition('试验', '通过', Fraction(1)),)
        fail_only = (Transition('试验', '失败', Fraction(1)),)
        tables = {}
        for bench in ('左台', '右台'):
            revision = revise(station, bench, states=['试验', '通过', '失败'],
                              start='试验', pass_state='通过', fail_state='失败',
                              transitions=fixed)
            tables[bench] = {
                'same': issue_mode(station, revision, {'high': pass_only, 'low': fail_only}),
                'low': issue_mode(station, revision, {'high': fail_only, 'low': pass_only}),
            }
        accepted = {}
        for bench, pair in tables.items():
            accepted[bench] = {name: accept_mode(intake, package, package.identity)
                               for name, package in pair.items()}
        station.close()
        with closing(connect_archive(archive_path)) as archive:
            initialize_archive(archive)
            same = sign_mode(intake, archive, accepted['左台']['same'],
                             accepted['右台']['same'], 1)
            opposite = sign_mode(intake, archive, accepted['左台']['same'],
                                 accepted['右台']['low'], 1)
    with closing(connect_archive(archive_path)) as reopened:
        initialize_archive(reopened)
        same = get_mode(reopened, same.report_id)
        opposite = get_mode(reopened, opposite.report_id)
    bound = lambda report: (report.draft.lower, report.draft.upper)
    if bound(same) != (MODE_HALF, MODE_HALF) or bound(opposite) != (0, 0):
        raise AssertionError('V2 mode demonstration signed unexpected bounds')
    print(f'V2 同档 {same.report_id} 截止 {same.draft.horizon} 下界 {same.draft.lower} '
          f'上界 {same.draft.upper} 首执 {same.receipt.receipt_id}')
    print(f'V2 反档 {opposite.report_id} 截止 {opposite.draft.horizon} 下界 '
          f'{opposite.draft.lower} 上界 {opposite.draft.upper} '
          f'首执 {opposite.receipt.receipt_id}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v2.mode_same_and_opposite',
        'input': {'每拍同档概率': '1/2', '截止拍': 1},
        'expected': {'same': ['1/2', '1/2'], 'opposite': ['0', '0']},
        'actual': {'same': [str(same.draft.lower), str(same.draft.upper)],
                   'opposite': [str(opposite.draft.lower), str(opposite.draft.upper)]},
        'downstream_expected': {'parents': [same.receipt.accepted_ids,
                                            opposite.receipt.accepted_ids]},
        'downstream_actual': {'parents': [same.receipt.accepted_ids,
                                          opposite.receipt.accepted_ids]},
        'code_paths': ['intake/modes.py', 'review/bounds.py', 'records/mode.py'],
        'oracle': '两台共用当拍档位。双高档同过为 1/2；一台高档、一台低档则错开。',
    }, ensure_ascii=False, sort_keys=True))


def main():
    with TemporaryDirectory(prefix='bench-review-') as temporary:
        directory = Path(temporary)
        station_path = directory / 'station.sqlite'
        archive_path = directory / 'archive.sqlite'
        with closing(IntakeCatalog(directory / 'intake.sqlite')) as intake:
            intake.initialize()
            left = prepare(station_path, intake, '左台', False)
            right = prepare(station_path, intake, '右台', False)
            with closing(connect_archive(archive_path)) as archive:
                initialize_archive(archive)
                old = sign(intake, archive, left, right)
                new_left = prepare(station_path, intake, '左台', True)
                new = sign(intake, archive, new_left, right)
        with closing(connect_archive(archive_path)) as reopened:
            initialize_archive(reopened)
            check_report('v4', old, get(reopened, old.report_id), Fraction(1, 3), (left, right),
                         {'尝试通过率': '1/2', '左台失败后额外等待': 0, '右台失败后额外等待': 0},
                         '两台最终都必过。同拍联合质量为首项 1/4、公比 1/4 的几何级数。')
            check_report('v7.shifted_retry', new, get(reopened, new.report_id), Fraction(2, 7),
                         (new_left, right),
                         {'尝试通过率': '1/2', '左台失败后额外等待': 1, '右台失败后额外等待': 0},
                         '左台只在奇数拍尝试。同拍联合质量为首项 1/4、公比 1/8 的几何级数。')
        run_mode_demo(directory)


if __name__ == '__main__':
    main()
