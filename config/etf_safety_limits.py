"""
平安证券ETF条件单 - 硬编码安全红线
最后更新：2026-05-28
任何情况下不可突破，代码级强制执行
"""

SAFETY_LIMITS = {
    # 单笔上限
    "max_single_trade_pct_of_total": 0.10,      # 单笔不超过总资产10%
    # 单日上限
    "max_daily_trade_pct_of_total": 0.25,       # 单日累计不超过总资产25%
    "max_daily_trades": 10,                      # 单日最多10笔
    "max_daily_condition_orders": 50,            # 单日最多50个条件单
    # 现金保护
    "min_cash_reserve_pct": 0.05,               # 至少保留5%现金
    # 绝对禁止
    "no_margin_trading": True,                   # 禁止融资融券
    "no_after_hours_trading": True,              # 禁止盘后交易
    # 人工确认
    "require_human_confirm": True,               # 必须人工审核
    # 批量控制
    "max_conditions_per_signal": 15,             # 单信号最多15个条件单
    "reservation_buffer_pct": 0.02,             # 预约缓冲2%
    # 滑点与行情
    "max_slippage_pct_default": 0.003,          # 默认最大滑点0.3%
    "quote_staleness_seconds": 9,               # 行情超时9秒（3秒*3次）
    # 自动撤单
    "auto_cancel_seconds": 30,                   # 未成交30秒自动撤单
}