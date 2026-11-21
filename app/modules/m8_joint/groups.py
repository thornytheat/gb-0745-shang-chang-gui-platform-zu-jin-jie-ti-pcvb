from app.errors import InputError
from app.modules.m1_intake.amounts import cents
from app.modules.m1_intake.service import transaction_key

GROUP_FIELDS = ("group", "members", "tiers")


def normalize_groups(records):
    """校验联合分组账簿。结构错误整批拒绝（沿用既有 BAD_GROUP）。"""
    groups = []
    keys = set()
    for item in records:
        try:
            name = transaction_key(item, "group")
            if name in keys:
                raise ValueError("分组重复")
            members = item["members"]
            if not isinstance(members, list) or not members:
                raise ValueError("成员为空")
            shops = []
            for member in members:
                shop = transaction_key({"shop": member}, "shop") if not isinstance(member, dict) else transaction_key(member, "shop")
                if shop in shops:
                    raise ValueError("成员柜台重复")
                shops.append(shop)
            rates = item["tiers"]
            if not isinstance(rates, list) or not rates or len(rates) > 12 or rates[-1][0] is not None:
                raise ValueError("阶梯须以无上限档结束")
            tiers = []
            previous = 0
            for index, (ceiling, points) in enumerate(rates):
                limit = cents(ceiling) if ceiling is not None else None
                if (limit is None and index != len(rates) - 1) or (limit is not None and limit <= previous):
                    raise ValueError("档位边界须严格递增")
                if type(points) is not int or not 0 <= points <= 10000:
                    raise ValueError("费率须为万分比整数")
                tiers.append((limit, points))
                previous = limit
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise InputError("BAD_GROUP", "联合分组、成员或阶梯费率无效") from exc
        keys.add(name)
        groups.append({"group": name, "members": shops, "tiers": tiers})
    return sorted(groups, key=lambda row: row["group"])


def assert_disjoint_members(groups):
    """同一柜台不得同时挂在两个联合组：归属无法裁决，整批拒绝。"""
    ownership = {}
    for group in groups:
        for shop in group["members"]:
            if shop in ownership:
                raise InputError(
                    "BAD_GROUP", f"柜台 {shop} 同时属于联合组 {ownership[shop]} 与 {group['group']}"
                )
            ownership[shop] = group["group"]
