import copy

import pytest

from app.errors import InputError
from app.modules.m7_publish.service import calculate
from app.modules.m8_joint.groups import assert_disjoint_members, normalize_groups
from app.modules.m8_joint.service import settle_groups, _largest_remainder


@pytest.fixture(scope="session")
def joint_raw():
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "sample_data" / "joint.json").read_bytes()


@pytest.fixture(scope="session")
def joint_run(joint_raw):
    return calculate(joint_raw, "2026-11")


def test_combined_tier_uses_group_net_sales(joint_run):
    summary = joint_run["联合汇总"][0]
    assert summary["合计净销售分"] == 160000
    # 独立抽成 90+70=160；联合阶梯 1600 × 5% = 80
    assert (summary["独立抽成分"], summary["联合抽成分"], summary["优惠合计分"]) == (
        16000,
        8000,
        8000,
    )
    assert summary["状态"] == "可发布"


def test_benefit_follows_sales_weight_and_conserves(joint_run):
    members = joint_run["联合阶梯"][0]["柜台明细"]
    assert [(row["柜台"], row["承接优惠分"], row["联合调整分"]) for row in members] == [
        ("101", 4500, -4500),
        ("102", 3500, -3500),
    ]
    check = joint_run["联合勾稽"][0]
    assert check["差额分"] == 0
    assert check["分柜承接合计分"] == check["组优惠合计分"] == 8000


def test_original_counter_bills_remain_unchanged(joint_run):
    # 原分柜月结不被改写：101 仍应缴 90，102 仍应缴 70，联合调整另开
    bills = {row["柜台"]: row["应缴分"] for row in joint_run["柜台对账"]}
    assert bills == {"101": 9000, "102": 7000}
    check = joint_run["联合勾稽"][0]
    assert check["联合后应缴分"] == {"101": 4500, "102": 3500}
    assert sum(check["联合后应缴分"].values()) == joint_run["联合阶梯"][0]["联合抽成分"]


def test_largest_remainder_splits_without_losing_cents():
    # 100 分按 1:1:1 分，余数依次补分，总额守恒
    parts = _largest_remainder([1, 1, 1], 100)
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]
    assert _largest_remainder([0, 0], 50) == [0, 0]
    assert _largest_remainder([3, 7], 0) == [0, 0]


def test_no_benefit_when_combined_fee_is_higher(joint_raw):
    import json

    data = json.loads(joint_raw)
    data["groups"][0]["tiers"] = [["1000", 1000], [None, 2000]]
    run = calculate(json.dumps(data, ensure_ascii=False).encode(), "2026-11")
    result = run["联合阶梯"][0]
    assert result["优惠合计分"] == 0
    assert all(row["承接优惠分"] == 0 for row in result["柜台明细"])
    assert run["联合汇总"][0]["状态"] == "无跨档优惠"
    assert run["联合挂起"] is False


def _mutated(data, mutate):
    clone = copy.deepcopy(data)
    mutate(clone)
    import json

    return json.dumps(clone, ensure_ascii=False).encode()


def test_missing_contract_holds_publication(joint_raw):
    import json

    data = json.loads(joint_raw)
    data["groups"][0]["members"].append("999")
    run = calculate(json.dumps(data, ensure_ascii=False).encode(), "2026-11")
    result = run["联合阶梯"][0]
    assert result["是否挂起"] is True
    reason = result["挂起原因"][0]
    assert reason["原因码"] == "JOINT_MISSING_CONTRACT" and reason["柜台"] == ["999"]
    assert run["联合挂起"] is True
    # 挂起时不承接任何优惠，原账保留
    assert result["优惠合计分"] == 0
    assert all(row["状态"] == "挂起" and row["承接优惠分"] == 0 for row in result["柜台明细"])


def test_isolated_sales_hold_publication(joint_raw):
    import json

    data = json.loads(joint_raw)
    data["events"].append(
        {"id": "J-BAD", "shop": "102", "day": "2026-11-19", "amount": "N/A", "kind": "sale"}
    )
    run = calculate(json.dumps(data, ensure_ascii=False).encode(), "2026-11")
    result = run["联合阶梯"][0]
    assert result["是否挂起"] is True
    assert result["挂起原因"][0]["原因码"] == "JOINT_SALES_ISOLATED"
    assert result["挂起原因"][0]["柜台"] == ["102"]
    assert run["联合挂起"] is True


def test_bad_group_structure_rejects_whole_batch(joint_raw):
    import json

    data = json.loads(joint_raw)
    data["groups"][0]["tiers"] = [["1000", 1000]]  # 缺无上限档
    with pytest.raises(InputError) as exc:
        calculate(json.dumps(data, ensure_ascii=False).encode(), "2026-11")
    assert exc.value.code == "BAD_GROUP"


def test_duplicate_member_in_group_rejected(joint_raw):
    import json

    data = json.loads(joint_raw)
    data["groups"][0]["members"] = ["101", "101"]
    with pytest.raises(InputError) as exc:
        calculate(json.dumps(data, ensure_ascii=False).encode(), "2026-11")
    assert exc.value.code == "BAD_GROUP"


def test_counter_in_two_groups_rejected():
    groups = normalize_groups(
        [
            {"group": "G1", "members": ["101", "102"], "tiers": [[None, 500]]},
            {"group": "G2", "members": ["102"], "tiers": [[None, 500]]},
        ]
    )
    with pytest.raises(InputError) as exc:
        assert_disjoint_members(groups)
    assert exc.value.code == "BAD_GROUP"


def test_groups_section_optional_for_legacy_books(clean_run):
    assert clean_run["联合阶梯"] == []
    assert clean_run["联合汇总"] == []
    assert clean_run["联合挂起"] is False
    assert clean_run["联合勾稽"] == []


def test_settle_skips_members_without_period_rows():
    # 成员柜台当月零交易也可参与，净销售按 0 处理
    groups = [{"group": "G1", "members": ["101", "102"], "tiers": [(None, 500)]}]
    rows = [
        {"柜台": "101", "版本": "v", "净销售分": 100000, "抽成分": 10000, "应计分": 10000},
    ]
    contracts = [{"shop": "101"}, {"shop": "102"}]
    results, summaries = settle_groups(groups, rows, contracts, [], {}, "2026-11")
    detail = results[0]["柜台明细"]
    assert detail[1]["净销售分"] == 0 and detail[1]["承接优惠分"] == 0
    assert summaries[0]["合计净销售分"] == 100000
