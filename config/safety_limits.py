"""安全红线 - 硬编码，不可配置覆盖。

任何试图绕过这些限制的操作都会被拒绝并报警。
修改此文件需双人复核 + git blame 记录。
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SafetyLimits:
    """不可变安全红线。frozen=True 防止运行时篡改。"""

    # 单笔不超过总资产 10%
    max_single_trade_pct: Decimal = Decimal("0.10")

    # 单日交易不超过总资产 25%
    max_daily_trade_pct: Decimal = Decimal("0.25")

    # 单日最多 10 笔
    max_daily_trades: int = 10

    # 永远保留 5% 现金
    min_cash_reserve_pct: Decimal = Decimal("0.05")

    # 永远需要人工确认
    require_human_confirm: bool = True

    # 禁止融资融券
    no_margin_trading: bool = True

    # 禁止盘后交易
    no_after_hours: bool = True


# 全局单例，导入即用
LIMITS = SafetyLimits()


def validate_not_bypassed(limits: SafetyLimits) -> None:
    """启动时校验红线未被篡改。"""
    assert limits.require_human_confirm is True, "人工确认不可关闭"
    assert limits.no_margin_trading is True, "融资融券不可开启"
    assert limits.no_after_hours is True, "盘后交易不可开启"
    assert limits.min_cash_reserve_pct >= Decimal("0.05"), "现金储备不可低于5%"