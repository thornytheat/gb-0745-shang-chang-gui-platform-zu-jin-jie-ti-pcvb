from collections import defaultdict

from app.modules.m4_tiers.progressive import charge


def _largest_remainder(weights, total_cents):
    """按整数权重（净销售分）分配总额，零权重不参与，保证分毫不差。"""
    pool = sum(weights)
    if pool <= 0 or total_cents == 0:
        return [0] * len(weights)
    quotas = [total_cents * weight / pool for weight in weights]
    floors = [int(quota) for quota in quotas]
    remainder = total_cents - sum(floors)
    order = sorted(
        range(len(weights)),
        key=lambda index: (quotas[index] - floors[index], weights[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def _shop_nets(period_rows):
    """同一柜台多个合同版本的本月净销售合并到柜台口径。"""
    nets = defaultdict(int)
    for row in period_rows:
        nets[row["柜台"]] += row["净销售分"]
    return nets


def _standalone_commission(period_rows):
    """单柜独立账：按各合同版本原档位算出的抽成（保底另列，不参与联合优惠）。"""
    commission = defaultdict(int)
    for row in period_rows:
        commission[row["柜台"]] += row["抽成分"]
    return commission


def settle_groups(groups, period_rows, contracts, isolated, shop_by_line, period):
    """联合阶梯结算。

    档位依据：组内成员本月净销售合计，套用组合同的联合阶梯费率。
    优惠承接：联合抽成低于各柜独立抽成合计的差额，按各柜净销售以最大余数法
    分摊为「联合调整分」（负数=让惠）；原分柜月结账保持不变，调整另开。
    """
    nets = _shop_nets(period_rows)
    standalone = _standalone_commission(period_rows)
    contracted = {c["shop"] for c in contracts}
    isolated_shops = {
        shop_by_line[item["行号"]]
        for item in isolated
        if item["行号"] in shop_by_line
    }

    results, summaries = [], []
    for group in groups:
        members = group["members"]
        block_reasons = []

        missing = [shop for shop in members if shop not in contracted]
        if missing:
            block_reasons.append({"原因码": "JOINT_MISSING_CONTRACT", "柜台": missing})

        quarantined = sorted({shop for shop in isolated_shops if shop in members})
        if quarantined:
            block_reasons.append({"原因码": "JOINT_SALES_ISOLATED", "柜台": quarantined})

        member_nets = [max(nets.get(shop, 0), 0) for shop in members]
        combined_net = sum(member_nets)
        combined_fee, combined_bands = charge(combined_net, group["tiers"])
        independent_fee = sum(standalone.get(shop, 0) for shop in members)
        benefit_total = independent_fee - combined_fee

        blocked = bool(block_reasons)
        if blocked or benefit_total <= 0:
            allocations = [0] * len(members)
        else:
            allocations = _largest_remainder(member_nets, benefit_total)

        member_rows = []
        for shop, net_cents, allocation in zip(members, member_nets, allocations):
            member_rows.append(
                {
                    "柜台": shop,
                    "净销售分": net_cents,
                    "独立抽成分": standalone.get(shop, 0),
                    "联合调整分": -allocation if not blocked else 0,
                    "承接优惠分": allocation if not blocked else 0,
                    "状态": "挂起" if blocked else ("无优惠" if benefit_total <= 0 else "已承接"),
                }
            )

        result = {
            "联合组": group["group"],
            "账期": period,
            "档位依据": "组内柜台本月净销售合计",
            "合计净销售分": combined_net,
            "独立抽成分": independent_fee,
            "联合抽成分": combined_fee,
            "联合档段": combined_bands,
            "优惠合计分": max(benefit_total, 0) if not blocked else 0,
            "是否挂起": blocked,
            "挂起原因": block_reasons,
            "柜台明细": member_rows,
        }
        results.append(result)
        summaries.append(
            {
                "联合组": group["group"],
                "合计净销售分": combined_net,
                "独立抽成分": independent_fee,
                "联合抽成分": combined_fee,
                "优惠合计分": result["优惠合计分"],
                "状态": "挂起不发布" if blocked else ("无跨档优惠" if benefit_total <= 0 else "可发布"),
            }
        )
    return results, summaries
