# 榆川双台架复测核账

榆川材料试验中心复测室根据工程组提供的带权规程安排联合复核。白班登记并核定单台规程，夜班只凭实收件签发双台结果，档案员按号领取当时签存的结论。

## 运行

需要 Python 3.11 和标准库，无需安装依赖。在项目根目录运行完整回归和业务演示。

```text
python3 -B check_project.py
python3 -B demo.py
```

演示使用临时目录中的三份 SQLite 库。真实使用时由调用者为站库、实收库、档案库分别指定持久路径。同一核账域的两台规程归同一站库，固定修订号和报告号分别在各自的库内编号。数值和父件顺序由固定输入决定。复现编号还需要相同的库初始状态及调用顺序。初始化可重复执行并保留用户已经登记、接纳和签发的内容。初始化不装载样例。

## 一天的工作

1. 白班打开站库和实收库，夜班打开档案库，各调用一次初始化。
2. 规程员登记两台台架的初态、继续状态、通过与失败终局，以及工程组核定的转移概率。更正提交合法后成为当前修订，旧修订仍可按号获取。
3. 分析员指定修订号冻结单台分析，可核对首拍、任意指定有限拍、最终通过及永不通过质量。样例的两台每拍各有 1/2 机会通过，否则继续。
4. 分析员装件并按固定身份交接。夜班核对正文并接纳，保留实收编号。重复交付原件可领取同一编号。
5. 站库关闭后，夜班按左右顺序指定两个不同台架的实收号。样例的两台最终共同通过概率为 1，同拍首次通过的条件概率为 1/3。
6. 档案库同时签存原值、左右父件和首执。签发失败时这三类记录一起撤回。
7. 次日左台更正为每次尝试失败后多等一拍，再回去尝试。分析员按新修订另行冻结并接纳，夜班指定新左件和原右件签出 2/7。
8. 档案员重开档案库按号领取。旧号保留 1/3 和旧左件，新号保留 2/7 和新左件，两份报告各自保留首次签发的首执。

## Python API

```python
from contextlib import closing
from fractions import Fraction
from protocol import RevisionStore, Transition, revise
from analysis import freeze
from intake import IntakeCatalog, accept, pack
from records import connect_archive, initialize_archive, get
from review import sign

with closing(RevisionStore('station.sqlite')) as station:
    station.initialize()
    packages = []
    for bench in ('左台', '右台'):
        revision = revise(
            station, bench, states=('尝试', '通过', '失败'), start='尝试',
            pass_state='通过', fail_state='失败',
            transitions=(Transition('尝试', '通过', Fraction(1, 2)),
                         Transition('尝试', '尝试', Fraction(1, 2))),
        )
        metrics, frozen = freeze(station, revision)
        packages.append(pack(frozen))

with closing(IntakeCatalog('night.sqlite')) as intake:
    intake.initialize()
    left, right = (accept(intake, item, item.identity) for item in packages)
    with closing(connect_archive('archive.sqlite')) as archive:
        initialize_archive(archive)
        signed = sign(intake, archive, left, right)
        original = get(archive, signed.report_id)
        print(original.draft.joint_pass, original.draft.conditional_sync)
```

- `revise` 返回固定修订号。状态名和台架名是非空字符串。初态必须是非终局，每个非终局的整行权重和必须为 1，两种终局没有出边。同向边相加，零权边不贡献质量。非法输入抛出 `protocol.validate.InvalidProtocol`，当前指向和历史不变。`RevisionStore.current` 返回当前修订号或 `None`，`RevisionStore.get` 返回固定快照或 `None`。
- `freeze` 返回 `(SingleBenchMetrics, FrozenAnalysis)`，未知修订抛出 `KeyError`。可用 `analysis.first_pass.first_hit_mass(kernel, tick)` 核对正整数拍，其中 `frozen.passage` 是冻结件携带的匿名过程。站内也可由 `build_process(revision).kernel` 得到相同计算口径。
- `pack` 给出规范 JSON 字节和固定身份。`accept` 要求传入事先选定的预期 `PackageIdentity`，返回实收号。损坏、错绑、错误拍原点和质量摘要错误抛出 `intake.admit.InvalidPackage`。同号异件即使带有匹配新正文的摘要，也抛出 `intake.catalog.PackageConflict`。实收校验涵盖交换封装，签发时另行解释同步证据。
- `sign` 接受左、右实收号。缺件或同台架父件抛出 `intake.select.ParentSelectionError`，首达证据无法解释或和摘要矛盾时抛出 `review.sync.UnusableEvidence`。`before_commit` 是可选的无参数调用，位于正文、两父件与首执暂存之后及提交之前。该调用抛出的异常会原样返回，档案事务整体回滚。
- `sign` 和 `get` 的成果均有 `report_id`、`draft` 与 `receipt`。正文含共同最终通过概率 `joint_pass`、同拍联合质量 `synchronous`、同拍条件值 `conditional_sync` 和有序父件 `parents`。每个父件保留实收号、件号、台架、修订、分析号和摘要。`get` 只读档案库，未签发的编号返回 `None`。

## 概率口径

第 1 拍是初态的第一次转移。对继续矩阵 Q 和下一拍直接通过向量 r，第 t 拍首达质量是初态行向量乘 Q 的 t−1 次幂再乘 r。全体有限拍质量的总和是最终通过率，余量涵盖失败终局和永久无法通过的闭环。冻结件用件内索引记录匿名转移系数，站内状态名和逐拍历史留站。交换正文和档案用约分后的分子和正分母的十进制整数文本保存有理数。

两台独立，共同最终通过概率 C 为两台最终通过率之积。同拍质量 S 为每拍两台首达质量乘积之和。只有双方都继续，状态对才进入下一拍。同步域外的零贡献状态不进入方程求解，因而可以精确累计无限重试的尾部。C 大于零时条件值为 S/C，错拍可得到精确的 0。C 为零时条件值为 `None`，报告仍可合法签发。

`tests/test_protocol.py` 核对规程校验、固定修订及单台核定。`tests/test_intake.py` 核对接纳、固定身份和冲突。`tests/test_review.py` 核对断源同步、错拍、闭环、无资格及选件失败。`tests/test_records.py` 核对更正、原始父件顺序、精确领取与事务回滚。`tests/test_modes.py` 核对 V2 发行、实收、共享档位两界、拒绝边界及重开领取。

## 双档供压（V2）

`MODES.md` 描述夜班预留联检位使用的双档签报。固定修订的每个非终局状态、每个去向在两档上的等权平均必须等于原冻结修订，核对覆盖未走到的行。件内只保留匿名状态身份，两档共用同一身份，站内状态名和原表留站。

```python
from intake import issue_mode, accept_mode
from review import sign_mode
from records import get_mode

package = issue_mode(station, revision, {'high': high_edges, 'low': low_edges})
accepted = accept_mode(intake, package, package.identity)
signed = sign_mode(intake, archive, left, right, horizon=2)   # 关站后，仅用实收库
reopened = get_mode(archive, signed.report_id)               # 仅重开档案库
print(reopened.draft.lower, reopened.draft.upper)
```

- 每拍两台共用一次等概率档位抽取（各 1/2），相邻及非相邻拍相互独立，并独立于此前进程。给定当拍档位和截至上拍的完整双台历史，各台下一步的条件边际必须等于自身当前状态的档位行；两台的联合耦合在行边际内可任意选取。因此单拍期望成功是两行分布之间的运输问题极值。
- 只有第 1 至 `horizon` 拍内两台同拍首次进入通过终局才计入。任一侧提前单独通过或任一侧失败，该路径贡献为零。`lower`、`upper` 是在全部合法联合过程中的精确可达最小值和最大值；两者都是 `Fraction`，无同过路径时均为 0 仍签发。
- 件号 `P2-A2-…` 的同号异文抛 `intake.catalog.PackageConflict`；版本、身份、摘要或行守恒不符抛 `intake.modes.InvalidModePackage`（`ValueError`）。`accept` 继续只办理 V1，`accept_mode` 只办理 V2。`sign_mode` 要求两个不同台架的 V2 实收号，混版、缺件或坏证据均拒绝；V1 报告号 `SR-…` 与 V2 报告号 `SM-…` 可区分并在同一档案库并存。
- `before_commit` 与 V1 相同，位于报告、两父件与首执暂存之后及提交之前。异常原样传出，失败后无本次报告或首执，原请求可重办。`get_mode` 查无 V2 报告时返回 `None`。

`python3 -B mode_examples.py` 逐一发行并核对 `MODES.md` 对照样例，最后展示同一冻结修订发行同档与反档两件，关站后签出 `[1/2,1/2]` 与 `[0,0]`，再只重开档案领取。
