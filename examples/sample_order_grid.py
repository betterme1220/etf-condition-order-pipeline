from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)

if PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_TEXT)


from core.broker_adapter import (  # noqa: E402
    ChecklistError,
    ManualAppChecklistGenerator,
    render_checklist_text,
)


def main() -> None:
    """网格交易 APP 填写清单示例。

    说明：
    - 网格交易截图中未确认独立监控时段字段。
    - 因此必须显式设置 allow_grid_without_monitor_window=True。
    - 本示例仅生成“人工填写清单”，不自动下单、不自动点击 APP。
    """

    order = {
        "system_order_id": "ETF-20260609-510300-GRID-0001",
        "template_key": "grid_trading",
        "security_code": "510300",

        # 网格基础参数
        "initial_base_price": "3.850",
        "grid_pct": "1.0",
        "price_range_pct": "3.0",

        # A股/场内ETF交易规则
        "lot_size": 100,
        "tick_size": "0.001",

        # 当前持仓，后续应从 portfolio-monitor.xlsx 读取
        "current_position_qty": 500,

        # 模式一：手工指定单笔数量
        # "order_qty": 100,

        # 模式二：按项目资金和单次比例自动计算单笔数量
        # 单次金额 = 项目总资金 × 单次买入比例
        # 委托数量 = 单次金额 / 数量计算参考价，并向下取整到100整数倍
        "project_total_capital": "20000",
        "batch_buy_pct": "10",
        "quantity_reference_price": "3.850",
        "quantity_price_basis": "initial_base_price",

        # 允许上涨过程中全部卖出到0
        "allow_sell_to_zero": True,

        # 每周最多买入2次
        "weekly_max_buy_batches": 2,
        "weekly_used_buy_batches": 0,

        # 网格模板未确认独立监控时段。
        # 如果不显式开启，manual_app_checklist 会阻断生成清单。
        "allow_grid_without_monitor_window": True,

        # 当前只是生成草稿清单，不要求 human_approval_status=APPROVED
        "output_mode": "DRAFT",
    }

    generator = ManualAppChecklistGenerator()
    result = generator.generate(order)

    print(render_checklist_text(result))


if __name__ == "__main__":
    try:
        main()
    except ChecklistError as exc:
        print("生成网格交易清单失败：")
        print(exc)