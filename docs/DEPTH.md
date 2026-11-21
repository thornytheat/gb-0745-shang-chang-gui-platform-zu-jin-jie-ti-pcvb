# 深度登记 商场柜台租金阶梯

- 规模：中型
- 核心规则：版本保底与累进抽成后，将旧期退单定位到原版本、重新计算应计租金并入本期差额。
- 使用者与流程：商场运营结算员导入收银、合同、免租和缴款四本账，逐柜台核账并留档。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 收银采信 | 上传账簿，隔离坏金额和同号矛盾 | `GET /api/intake/{id}` | `app/modules/m1_intake/service.py` `app/modules/m1_intake/amounts.py` | `tests/test_intake.py` |
| M2 合同版本 | 查销售日的合同与重叠区间 | `GET /api/contracts/{id}` | `app/modules/m2_contracts/versions.py` `app/modules/m2_contracts/service.py` | `tests/test_contracts.py` |
| M3 免租日历 | 查看合同日历、免租日期和保底折算 | `GET /api/calendar/{id}` | `app/modules/m3_calendar/month.py` `app/modules/m3_calendar/service.py` | `tests/test_calendar.py` |
| M4 累进抽成 | 对照档位费用与版本保底 | `GET /api/tiers/{id}` | `app/modules/m4_tiers/progressive.py` `app/modules/m4_tiers/service.py` | `tests/test_tiers.py` |
| M5 退单回冲 | 追原流水，重算旧月应计差 | `GET /api/refunds/{id}` | `app/modules/m5_refunds/credits.py` `app/modules/m5_refunds/service.py` | `tests/test_refunds.py` |
| M6 租金勾稽 | 核对实缴、欠缴与账簿行数 | `GET /api/reconcile/{id}` | `app/modules/m6_reconcile/service.py` `app/modules/m6_reconcile/totals.py` | `tests/test_reconcile.py` |
| M8 联合阶梯 | 同商户多柜按合计销售跨档，另开联合调整并挂起异常组 | `GET /api/joint/{id}` | `app/modules/m8_joint/groups.py` `app/modules/m8_joint/service.py` | `tests/test_joint.py` `tests/test_joint_api.py` |
| M7 结算复核 | 从批次凭证复算、下载证据 | `GET /api/publish/{id}` `POST /api/runs` | `app/modules/m7_publish/service.py` `app/modules/m7_publish/export.py` `app/store.py` | `tests/test_publish.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-06 | `mall.json` 含全角千分位 | 金额解析失败 | 归一后严格解析 | `app/modules/m1_intake/amounts.py` | `tests/test_intake.py::test_normalized_thousands` |
| TRAP-07 | `mall.json` 收银金额为 N/A | 当零元销售 | 隔离带原因码 | `app/modules/m1_intake/service.py` | `tests/test_intake.py::test_missing_amount_isolated` |
| TRAP-08 | `mall.json` 柜台号有前导零 | 归并时丢失 | 按字符串处理 | `app/modules/m1_intake/service.py` | `tests/test_intake.py::test_leading_zero_and_long_id` |
| TRAP-10 | 费率产生半分 | 浮点或分档舍入偏差 | 按费率整数分子汇总后统一舍入 | `app/modules/m4_tiers/progressive.py` | `tests/test_tiers.py::test_half_up_precise` |
| TRAP-13 | `mall.json` 退单在原销售前读到 | 当成缺原单 | 按业务日归档后匹配 | `app/modules/m5_refunds/credits.py` | `tests/test_refunds.py::test_out_of_order_matched` |
| TRAP-14 | `mall.json` 重复流水金额有差 | 后写覆盖 | 两条都隔离并保留行号 | `app/modules/m1_intake/service.py` | `tests/test_intake.py::test_conflicts_quarantine_both` |
| TRAP-15 | `mall.json` 金额单位混合 | 误把分计为元 | 归一为整数分 | `app/modules/m1_intake/amounts.py` | `tests/test_intake.py::test_unit_is_applied` |
| TRAP-16 | `clean.json` 剪去尾部 | 误入部分交易 | 拒绝结构断裂 | `app/modules/m1_intake/reader.py` | `tests/test_intake.py::test_truncated_source` |
| TRAP-17 | `mall.json` 旧金额字段 gross | 旧账对应列丢失 | 显式兼容别名 | `app/modules/m1_intake/service.py` | `tests/test_intake.py::test_legacy_alias` |
| TRAP-23 | `mall.json` 合同于当日切换 | 同时计入两个版本 | 合同 `[start,end)` | `app/modules/m2_contracts/versions.py` | `tests/test_contracts.py::test_adjacent_boundaries` |
| TRAP-25 | `mall.json` 存在合同重叠 | 随机采信一版 | 暂停区间内归属并提供冲突区间 | `app/modules/m2_contracts/service.py` | `tests/test_contracts.py::test_overlap_not_guessed` |
| TRAP-31 | 同商户柜台合计销售才跨档，单柜月结各自独立 | 只按单柜丢优惠，或统一降档让低销售柜分走优惠 | 保留分柜原账，另开联合调整；优惠按各柜净销售以最大余数法分摊，挂起组不发布 | `app/modules/m8_joint/service.py` | `tests/test_joint.py::test_benefit_follows_sales_weight_and_conserves` |

## 联合阶梯（M8）

- 账簿可选第五段 `groups`：`group`、`members`（柜台字符串数组）、`tiers`（与合同同构，末档无上限）。同一柜台不得挂在两个组，结构错误以 `BAD_GROUP` 整批拒绝，沿用失败不写入旧批次。
- 档位依据是组内成员**本月净销售合计**，对组联合阶梯重新计费；组费高于独立抽成合计时不产生调整，低销售柜台不会分走不属于自己的优惠。
- 分柜月结账（保底、抽成、旧期回冲、应缴）原样保留。联合优惠作为单独的「联合调整分」按各柜净销售权重用最大余数法分摊，余数补给权重高的柜台，分柜承接合计与组优惠合计逐分勾稽。
- 成员合同缺失（`JOINT_MISSING_CONTRACT`）或成员存在隔离记录（`JOINT_SALES_ISOLATED`）时该组标记挂起：联合调整一律为零、看板与结算页提示，批次 `联合挂起=true`，待账簿补齐后重跑；重跑命中同一输入摘要时复用旧批次，不新增结果。

## 验算

`clean.json` 中 001 柜台的本月净销售 1400 元。前 1000 元按 10%，后 400 元按 20%，抽成为 180 元。旧期退单使原月应计由 130 调整为 110 元。本期应缴 160 元，与实缴平账。

## 已知不覆盖

单场结算以自然月为周期，适用内置样本所示合同规则。跨币种租金、税额和历史更正审批由外围系统处理。
