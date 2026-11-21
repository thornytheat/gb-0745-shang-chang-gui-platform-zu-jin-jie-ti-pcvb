from time import perf_counter

from app.manifest import input_identity, manifest
from app.modules.m1_intake.reader import open_payload
from app.modules.m1_intake.service import parse_events
from app.modules.m2_contracts.service import bind_sales, version_conflicts
from app.modules.m2_contracts.versions import normalize_contracts
from app.modules.m3_calendar.month import month_window
from app.modules.m3_calendar.service import exempt_days
from app.modules.m4_tiers.service import calculate_period
from app.modules.m5_refunds.credits import deduction_map, match_refunds
from app.modules.m5_refunds.service import previous_period_credits
from app.modules.m6_reconcile.service import parse_statement, reconcile, row_balance
from app.modules.m6_reconcile.totals import summary
from app.modules.m8_joint.groups import assert_disjoint_members, normalize_groups
from app.modules.m8_joint.service import settle_groups
from app.reasons import issue


def joint_conservation(joint_results, reconcile_rows):
    """整组账与分柜月结勾稽：承接优惠逐柜加总必须等于组优惠合计。"""
    checks = []
    due_by_shop = {row["柜台"]: row["应缴分"] for row in reconcile_rows}
    for result in joint_results:
        members = result["柜台明细"]
        allocated = sum(item["承接优惠分"] for item in members)
        checks.append(
            {
                "联合组": result["联合组"],
                "组优惠合计分": result["优惠合计分"],
                "分柜承接合计分": allocated,
                "差额分": result["优惠合计分"] - allocated,
                "分柜应缴分": {item["柜台"]: due_by_shop.get(item["柜台"], 0) for item in members},
                "联合后应缴分": {
                    item["柜台"]: due_by_shop.get(item["柜台"], 0) - item["承接优惠分"]
                    for item in members
                },
                "是否挂起": result["是否挂起"],
            }
        )
    return checks


def calculate(raw, period):
    run_id, parameters = input_identity(raw, period)
    started = perf_counter()
    start, end = month_window(period)
    raw_data = open_payload(raw)
    contracts = normalize_contracts(raw_data["contracts"])
    groups = normalize_groups(raw_data.get("groups", []))
    assert_disjoint_members(groups)
    exempt = exempt_days(raw_data["holidays"])
    statement = parse_statement(raw_data["statement"])
    events, isolated, duplicates = parse_events(raw_data["events"])
    # 隔离原因码本身不带柜台，直接从原始账簿行号回溯柜台归属（含金额坏行）
    shop_by_line = {
        line: str(row.get("shop", "")).strip()
        for line, row in enumerate(raw_data["events"], 1)
        if str(row.get("shop", "")).strip()
    }
    shop_by_line.update({event["line"]: event["shop"] for event in events})
    after_cut = [issue(e["line"], "AFTER_CUT", e["id"]) for e in events if e["day"] >= end]
    in_cut = [e for e in events if e["day"] < end]
    sales, invalid_sales = bind_sales(contracts, in_cut)
    refunds, invalid_refunds = match_refunds(in_cut, sales)
    isolated = sorted(isolated + invalid_sales + invalid_refunds, key=lambda item: item["行号"])
    current, exceptions = calculate_period(sales, deduction_map(refunds, end), contracts, exempt, period)
    credits = previous_period_credits(sales, refunds, contracts, exempt, start, end)
    rows = reconcile(current, credits, statement)
    joint_results, joint_summary = settle_groups(
        groups, current, contracts, isolated, shop_by_line, period
    )
    joint_hold = any(item["是否挂起"] for item in joint_results)
    joint_checks = joint_conservation(joint_results, rows)
    balance = row_balance(len(raw_data["events"]), sales, refunds, isolated, duplicates, after_cut)
    return {
        "运行清单": manifest(run_id, parameters, round((perf_counter() - started) * 1000)),
        "行数勾稽": balance,
        "金额勾稽": summary(rows),
        "柜台对账": rows,
        "联合阶梯": joint_results,
        "联合汇总": joint_summary,
        "联合挂起": joint_hold,
        "联合勾稽": joint_checks,
        "分段核算": current,
        "旧期回冲": credits,
        "收银行": [
            {
                "行号": e["line"],
                "流水号": e["id"],
                "柜台": e["shop"],
                "销售日": e["day"].isoformat(),
                "销售分": e["cents"],
                "版本": e["version"],
            }
            for e in sales.values()
        ],
        "合同版本": [
            {
                "柜台": c["shop"],
                "版本": c["version"],
                "起始": c["start"].isoformat(),
                "截止": c["end"].isoformat(),
                "月保底分": c["floor"],
            }
            for c in contracts
        ],
        "合同时窗冲突": version_conflicts(contracts),
        "日历例外": exceptions,
        "隔离": isolated,
        "重复": duplicates,
        "截止后": after_cut,
        "退单证据": [
            {
                "行号": r["line"],
                "流水号": r["id"],
                "柜台": r["shop"],
                "原单号": r["origin"],
                "入账日": r["day"].isoformat(),
                "退单分": r["cents"],
            }
            for r in refunds
        ],
    }


def run_once(store, raw, period):
    key, _ = input_identity(raw, period)
    existing = store.get(key)
    if existing is not None:
        return existing, True
    return store.save(key, calculate(raw, period))
