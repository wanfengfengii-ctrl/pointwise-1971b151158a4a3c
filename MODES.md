# 双档供压签报

夜班用报告里的下界决定是否预留联检位。旧报告仍供采用独立规程的台架使用。沿用 README.md 的站库、实收库、档案库分工。每个实例仍办理一个核账域，同域台架在同一站库登记，来件汇入该域实收库。

## 发行和实收

- `intake.issue_mode(station, revision_id, modes)` 按 `RevisionStore` 中的固定修订返回 `intake.pack.Package`。`modes` 是恰有 `high` 和 `low` 两键的映射，各值为 `Transition` 的可迭代对象。两档沿用该修订的初态、状态集合及通过和失败终局。每档自身须满足 V1 规程行校验。同向边相加，零权边不贡献质量。各非终局状态的每个去向，包括两个终局，在两档的等权平均必须等于固定修订，未走到的行也核对。未知修订抛 `KeyError`，档位表非法或不合规程抛 `ValueError`。
- 件中保全两个档位的可解释匿名转移、初态和终局角色。件内同一状态在两档使用同一身份，`high` 与 `low` 的含义在所有件间一致。件采用 `intake.codec` 的规范 JSON 字节和精确分数编码，正文标识 V2，并携带与 `PackageIdentity` 相符的 `package_id`、`analysis_id`、`bench_id`、`revision_id`。身份的 `digest` 为规范正文的 SHA-256。匿名证据的内部行布局自行选择。
- 同一冻结修订上，归一后等价的档位表重复发行得到相同身份和字节。档位语义不同的表各自发行，仍指向各自采用的原冻结修订。V1 和 V2 件号可在同一实收库共存。
- `intake.accept_mode(catalog, package, expected)` 接收 V2 件及事先选定的 `PackageIdentity`，返回实收号。核对版本、身份、摘要及匿名转移的结构和质量守恒。错绑、坏件、同号异文或传入 V1 抛 `ValueError`。完全相同的件重交沿用首次实收号。既有 `accept`、`sign` 继续办理 V1，V2 不得按 V1 口径静默办理。

## 离线签发和领取

- `review.sign_mode(catalog, archive, left_id, right_id, horizon, *, before_commit=None)` 凭两个不同台架的指定 V2 实收号办理。父件按入参左右顺序保存。`horizon` 是从 1 起算的正整数截止拍，布尔值非法。坏证据、缺件、同台架或混版均抛 `ValueError`。签发只使用所选实收件，无需站库。
- 两侧从各自初态出发。每拍抽取一个 `high` 或 `low`，各占 1/2，相邻及非相邻拍的档位抽取相互独立，也独立于该拍以前的双台进程。两台在这一拍共享抽到的档位。给定当拍档位和截至上拍的完整双台历史，下一步在两台各自的投影上仍须恰好符合各自当前状态的档位行。允许在这两个投影约束内自由联合。合法过程不能使用未来档位做当拍选择。
- 第 1 拍是从初态作第一次转移。第 1 拍至 `horizon` 拍内同时首次进入各自通过终局才记为成功。任意一侧提前单独通过或两侧任一失败，这条路径整段贡献为零。返回在上述所有合法联合过程中的精确可达下界和上界。无同过路径时两界均为 `Fraction(0)`，仍签发。
- 返回值提供 `report_id`、`draft`、`receipt`。`draft` 提供 `horizon`、`lower`、`upper` 和两个按序的 `parents`。两界为 `Fraction`，父件使用现有 `ParentReference`，首执沿用 `Receipt` 字段。结果、截止拍、父件和首执一并提交。`before_commit` 和 V1 一样在全部暂存之后及提交之前调用。异常原样传出，失败后无本次报告或首执，原请求可重办。
- `records.get_mode(archive, report_id)` 只打开档案库即可领取签时存值，查无 V2 报告时返回 `None`。新旧报告号可以区分，沿用 `initialize_archive` 初始化或打开已有库。更正及重开后，既有报告返回原结论、父件及首执。

## 对照样例

`python3 -B mode_examples.py` 执行样例的 V1 冻结、实收和旧报告读回。脚本中的 `EXAMPLES` 提供档位转移和预期界，`example.left.register(station, '左台')` 返回用于发行的冻结号，`example.left.modes()` 给出传入 `issue_mode` 的表。右台用 `example.right`。用 `accept_mode(catalog, package, package.identity)` 分别接纳后关闭站库，以 `sign_mode(catalog, archive, left_id, right_id, horizon)` 签发。关闭实收库和档案库后重开档案，使用 `get_mode` 领取。两只短案可用同一右台冻结号分别发行 `HIGH_ONLY.modes()` 和 `LOW_ONLY.modes()`。

| 样例 | V2 预期 |
|---|---|
| `same_mode` 与 `opposite_modes` | 两份 V1 同拍质量都是 1/4。第 1 拍，双高通过签 `[1/2,1/2]`，右台改为仅低档通过则签 `[0,0]`。 |
| `conditioned_rows` | 首拍左到 A 或 B，右必到 R。次拍 A 必过、B 必败，R 在每种已发生历史下均半数通过。截止 1 拍签 `[0,0]`，截止 2 拍签 `[1/4,1/4]`。 |
| `free_joint` | 两档均允许把两台的一半通过质量配在一起或完全错开，截止 1 拍签 `[0,1/2]`。 |
| `continuation_choice` | 两台首拍均继续，A 及 B 各半。只有两台同时到 A 才能次拍同过，截止 2 拍签 `[0,1/2]`。 |
| `fresh_mode` | 高档两台必过，低档两台各自继续。截止 1 拍签 `[1/2,1/2]`，截止 2 拍签 `[3/4,3/4]`。 |

验收时可将 `conditioned_rows` 右台高档 R 的通过和失败改为 1/4 与 3/4，低档照旧，核对发行拒绝。核对错绑、同号异文、混版及 `before_commit` 故障。故障后的原样重办可正常签发，重开后与 V1 报告并存并按各自编号领取。

沿用 `.pointwise-runtime.json` 的 CLI 清单。`version` 为 1，`kind` 为 `cli`，`setup` 在无需安装时为空列表，`verify` 和 `demo` 各填一条 argv 列表。`verify` 应覆盖原全套回归并实际核对新业务结果及拒绝边界，失败返回非零。`demo` 应展示关站后两种档位关系的签报差异，以及只重开档案后的领取。
