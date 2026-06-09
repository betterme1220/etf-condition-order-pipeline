from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path


def _find_project_root(start: Path) -> Path:
    """从当前文件位置向上寻找项目根目录。

    判断依据：
    - 存在 core/
    - 存在 config/
    """

    current = start.resolve()

    if current.is_file():
        current = current.parent

    for path in [current, *current.parents]:
        if (path / "core").is_dir() and (path / "config").is_dir():
            return path

    raise RuntimeError(
        "无法定位项目根目录。请确认当前脚本位于项目目录内，"
        "且项目根目录下存在 core/ 和 config/。"
    )


def _positive_decimal_text(value: str) -> str:
    """argparse 用：校验输入是大于 0 的数字，但仍返回字符串。"""

    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:
        raise argparse.ArgumentTypeError(
            f"必须是有效数字，当前: {value!r}"
        ) from exc

    if decimal_value <= 0:
        raise argparse.ArgumentTypeError(
            f"必须大于0，当前: {value!r}"
        )

    return str(value)


try:
    PROJECT_ROOT = _find_project_root(Path(__file__))

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

except RuntimeError as exc:
    print("项目根目录定位失败：")
    print(exc)
    raise SystemExit(1) from exc


try:
    from core.broker_adapter import ChecklistError  # noqa: E402
    from core.generator.portfolio_order_builder import (  # noqa: E402
        PortfolioOrderBuilderError,
        build_grid_checklist_text_from_portfolio,
    )

except ModuleNotFoundError as exc:
    print("模块导入失败：")
    print(exc)
    print("")
    print("请确认当前项目结构包含：")
    print("- core/broker_adapter/")
    print("- core/generator/portfolio_order_builder.py")
    print("- config/")
    raise SystemExit(1) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "从 portfolio-monitor.xlsx 读取 ETF 行，"
            "生成平安 APP 网格交易人工填写清单。"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "退出码说明：\n"
            "  0  成功生成清单\n"
            "  1  未知错误 / 项目结构错误 / 模块导入失败\n"
            "  2  持仓表读取或字段校验失败\n"
            "  3  APP清单生成校验失败\n\n"
            "示例：\n"
            "  python examples\\sample_order_from_portfolio.py\n"
            "  python examples\\sample_order_from_portfolio.py 159915\n"
            "  python examples\\sample_order_from_portfolio.py 510300 --percentage-input-mode percentage_points\n"
            "  python examples\\sample_order_from_portfolio.py 510300 --portfolio-path \"D:\\Finance\\portfolio-monitor\\data\\portfolio-monitor.xlsx\"\n"
        ),
    )

    parser.add_argument(
        "security_code",
        nargs="?",
        default="510300",
        help="证券代码，例如 510300。默认 510300。",
    )

    parser.add_argument(
        "--portfolio-path",
        default=None,
        help=(
            "持仓 Excel 路径。"
            "不传时读取 config/portfolio_source.yaml 中的 portfolio_source.path。"
        ),
    )

    parser.add_argument(
        "--sheet-name",
        default=None,
        help=(
            "工作表名称。"
            "不传时读取 config/portfolio_source.yaml 中的 sheet/sheet_name。"
        ),
    )

    parser.add_argument(
        "--grid-pct",
        default="1.0",
        type=_positive_decimal_text,
        help="网格百分比，默认 1.0，表示 1%。",
    )

    parser.add_argument(
        "--price-range-pct",
        default="3.0",
        type=_positive_decimal_text,
        help="价格区间上下百分比，默认 3.0，表示上下 3%。",
    )

    parser.add_argument(
        "--percentage-input-mode",
        default="auto",
        choices=["auto", "percentage_points", "excel_fraction"],
        help=(
            "Excel 百分比解释模式。\n"
            "auto=根据单元格格式判断；\n"
            "percentage_points=10表示10%%，0.1表示0.1%%；\n"
            "excel_fraction=0.1表示10%%。"
        ),
    )

    parser.add_argument(
        "--strict-grid-window",
        action="store_true",
        help=(
            "严格执行内部监控窗口纪律，不允许网格模板绕过监控窗口。\n"
            "注意：当前网格模板尚未确认独立监控时段字段，开启后通常会触发安全阻断。"
        ),
    )

    parser.add_argument(
        "--allow-asymmetric-batch-pct-record-only",
        action="store_true",
        help=(
            "允许 单次买入比例%% 与 单次卖出比例%% 不一致，"
            "但卖出比例仅记录，不参与当前 MVP 网格清单数量计算。"
        ),
    )

    parser.add_argument(
        "--order-id-suffix",
        default=None,
        help="自定义订单ID后缀。默认使用随机后缀。",
    )

    parser.add_argument(
        "--save-output",
        default=None,
        help="可选：把生成的清单文本保存到指定 .txt 文件。",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        text = build_grid_checklist_text_from_portfolio(
            args.security_code,
            portfolio_path=args.portfolio_path,
            sheet_name=args.sheet_name,
            grid_pct=args.grid_pct,
            price_range_pct=args.price_range_pct,
            allow_grid_without_monitor_window=not args.strict_grid_window,
            output_mode="DRAFT",
            percentage_input_mode=args.percentage_input_mode,
            allow_asymmetric_batch_pct_record_only=(
                args.allow_asymmetric_batch_pct_record_only
            ),
            order_id_suffix=args.order_id_suffix,
        )

    except PortfolioOrderBuilderError as exc:
        print("从持仓表生成网格交易清单失败：")
        print(exc)
        return 2

    except ChecklistError as exc:
        print("网格交易清单校验失败：")
        print(exc)
        return 3

    except Exception as exc:
        print("生成清单时发生未知错误：")
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(text)

    if args.save_output:
        output_path = Path(args.save_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        print("")
        print(f"清单已保存：{output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())