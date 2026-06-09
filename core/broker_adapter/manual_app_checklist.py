from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from config import get_broker_capabilities


Side = Literal["buy", "sell"]
TemplateKey = Literal["daily_pct_change", "grid_trading"]

APP_DEFAULT_FULL_DAY_START = "09:30"
APP_DEFAULT_FULL_DAY_END = "14:57"

DEFAULT_MAX_DAILY_PCT_VALUE = Decimal("10")
DEFAULT_MIN_MONITOR_MINUTES = 5


class ChecklistError(Exception):
    """APP 填写清单生成失败。"""

    pass


@dataclass(frozen=True)
class ChecklistResult:
    template_key: str
    template_name: str
    app_entry_path: list[str]
    fields: list[dict[str, Any]]
    manual_confirm_items: list[str]
    warnings: list[str]
    screenshots_required: list[str]
    requires_manual_approval: bool = True
    context: dict[str, Any] = field(default_factory=dict)


def _context(order: dict[str, Any], template_key: str | None = None) -> str:
    system_order_id = order.get("system_order_id", "UNKNOWN_ORDER_ID")
    security_code = order.get("security_code", "UNKNOWN_SECURITY")
    template = template_key or order.get("template_key", "UNKNOWN_TEMPLATE")
    return (
        f"system_order_id={system_order_id}, "
        f"security_code={security_code}, "
        f"template={template}"
    )


def _required(
    order: dict[str, Any],
    field_name: str,
    *,
    template_key: str | None = None,
) -> Any:
    value = order.get(field_name)
    if value is None or value == "":
        raise ChecklistError(
            f"内部订单缺少必填字段: {field_name}; {_context(order, template_key)}"
        )
    return value


def _to_decimal(value: Any, field_name: str, *, context: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ChecklistError(
            f"字段不是有效数字: {field_name}={value!r}; {context}"
        ) from exc

    if result.is_nan():
        raise ChecklistError(f"字段不能是 NaN: {field_name}={value!r}; {context}")

    return result


def _to_positive_decimal(value: Any, field_name: str, *, context: str) -> Decimal:
    result = _to_decimal(value, field_name, context=context)
    if result <= 0:
        raise ChecklistError(f"字段必须大于0: {field_name}={value!r}; {context}")
    return result


def _to_int(value: Any, field_name: str, *, context: str) -> int:
    try:
        result = int(value)
    except Exception as exc:
        raise ChecklistError(
            f"字段不是有效整数: {field_name}={value!r}; {context}"
        ) from exc

    return result


def _to_non_negative_int(value: Any, field_name: str, *, context: str) -> int:
    result = _to_int(value, field_name, context=context)
    if result < 0:
        raise ChecklistError(f"字段不能为负数: {field_name}={value!r}; {context}")
    return result


def _to_positive_int(value: Any, field_name: str, *, context: str) -> int:
    result = _to_int(value, field_name, context=context)
    if result <= 0:
        raise ChecklistError(f"字段必须大于0: {field_name}={value!r}; {context}")
    return result


def _floor_to_lot(qty: int, lot_size: int) -> int:
    if qty <= 0:
        return 0
    return qty // lot_size * lot_size


def _validate_lot_aligned(
    qty: int,
    lot_size: int,
    field_name: str,
    *,
    context: str,
) -> None:
    if lot_size <= 0:
        raise ChecklistError(f"lot_size 必须大于0: lot_size={lot_size}; {context}")

    if qty % lot_size != 0:
        raise ChecklistError(
            f"{field_name} 必须是 lot_size 的整数倍: "
            f"{field_name}={qty}, lot_size={lot_size}; {context}"
        )


def _round_to_tick(value: Decimal, tick_size: Decimal = Decimal("0.001")) -> Decimal:
    if tick_size <= 0:
        raise ChecklistError(f"tick_size 必须大于0: tick_size={tick_size}")

    ticks = (value / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (ticks * tick_size).quantize(tick_size)


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _normalize_time(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 5 and text[2] == ":":
        return text
    if len(text) == 8 and text[2] == ":" and text[5] == ":":
        return text[:5]
    raise ChecklistError(f"时间格式必须为 HH:MM 或 HH:MM:SS，当前: {value!r}")


def _time_to_minutes(value: str) -> int:
    text = _normalize_time(value)
    hour_text, minute_text = text.split(":")
    hour = int(hour_text)
    minute = int(minute_text)

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ChecklistError(f"时间超出范围: {value!r}")

    return hour * 60 + minute


def _window_duration_minutes(monitor_start: str, monitor_end: str) -> int:
    return _time_to_minutes(monitor_end) - _time_to_minutes(monitor_start)


def _is_app_full_day_window(monitor_start: str, monitor_end: str) -> bool:
    """平安 APP 截图中的默认全天策略时段。"""
    return (
        _normalize_time(monitor_start) == APP_DEFAULT_FULL_DAY_START
        and _normalize_time(monitor_end) == APP_DEFAULT_FULL_DAY_END
    )


def _first_value(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return None
        return value[0]
    return value


def _get_default_values(template: dict[str, Any]) -> dict[str, Any]:
    default_values = template.get("default_output_values")
    if not isinstance(default_values, dict):
        return {}
    return default_values


def _get_observed_values(template: dict[str, Any]) -> dict[str, Any]:
    observed_values = template.get("observed_supported_values")
    if not isinstance(observed_values, dict):
        return {}
    return observed_values


def _resolve_order_qty(
    order: dict[str, Any],
    *,
    price: Decimal,
    context: str,
) -> int:
    """解析委托数量。

    优先级：
    1. order_qty 手工指定。
    2. project_total_capital × batch_buy_pct 自动计算。

    A 股 / 场内 ETF 默认最小交易单位为 100，最终数量必须向下取整到 lot_size 整数倍。
    """

    lot_size = _to_positive_int(order.get("lot_size", 100), "lot_size", context=context)

    if order.get("order_qty") not in (None, ""):
        order_qty = _to_positive_int(order["order_qty"], "order_qty", context=context)
        _validate_lot_aligned(order_qty, lot_size, "order_qty", context=context)
        return order_qty

    project_total_capital = _to_positive_decimal(
        order.get("project_total_capital"),
        "project_total_capital",
        context=context,
    )

    batch_buy_pct = _to_positive_decimal(
        order.get("batch_buy_pct"),
        "batch_buy_pct",
        context=context,
    )

    if batch_buy_pct > Decimal("100"):
        raise ChecklistError(
            f"单次买入比例不能超过100%: batch_buy_pct={batch_buy_pct}; {context}"
        )

    batch_amount = project_total_capital * batch_buy_pct / Decimal("100")
    raw_qty = int(batch_amount / price)
    order_qty = _floor_to_lot(raw_qty, lot_size)

    if order_qty < lot_size:
        raise ChecklistError(
            f"按项目资金和比例计算后数量不足一手: "
            f"project_total_capital={project_total_capital}, "
            f"batch_buy_pct={batch_buy_pct}, "
            f"price={price}, "
            f"raw_qty={raw_qty}, "
            f"lot_size={lot_size}; {context}"
        )

    return order_qty


def _resolve_daily_order_price(
    template: dict[str, Any],
    side: Side,
    *,
    context: str,
) -> tuple[str, str, list[str]]:
    """解析日涨跌幅模板委托价格。

    兼容新版配置：
        observed_supported_values.order_price_by_side.buy.expected_value
        observed_supported_values.order_price_by_side.sell.expected_value

    兼容旧版配置：
        observed_supported_values.buy_order_price
        observed_supported_values.sell_order_price
    """

    warnings: list[str] = []
    observed = _get_observed_values(template)

    by_side = observed.get("order_price_by_side")
    if isinstance(by_side, dict):
        side_cfg = by_side.get(side)
        if isinstance(side_cfg, dict):
            expected_value = side_cfg.get("expected_value")
            confirmation_status = side_cfg.get("confirmation_status", "unknown")
            if expected_value:
                return str(expected_value), str(confirmation_status), warnings

    legacy_key = "buy_order_price" if side == "buy" else "sell_order_price"
    legacy_value = _first_value(observed.get(legacy_key))

    if legacy_value:
        warnings.append(
            f"配置使用旧字段 observed_supported_values.{legacy_key}，"
            f"建议升级为 order_price_by_side.{side}.expected_value；{context}"
        )
        return str(legacy_value), "legacy_config", warnings

    fallback_value = "即时卖一价" if side == "buy" else "即时买一价"
    warnings.append(
        f"配置中缺少日涨跌幅 {side} 方向委托价格字段，"
        f"系统临时按交易逻辑提示为“{fallback_value}”。"
        f"人工必须在 APP 中确认该价格选项存在；{context}"
    )
    return fallback_value, "fallback_inferred_missing_config", warnings


class ManualAppChecklistGenerator:
    """根据内部订单生成平安 APP 人工填写清单。

    当前版本只生成清单，不做券商 API 下单、不自动点击 APP、不绕过人工审核。
    """

    def __init__(self) -> None:
        self.broker_cfg = get_broker_capabilities()
        self.templates = self.broker_cfg.get("condition_order_templates", {})
        self.output_policy = self.broker_cfg.get(
            "adapter_output_policy",
            {
                "screenshot_required": {
                    "before_submit": True,
                    "after_created": True,
                    "after_trigger_or_end": True,
                }
            },
        )

    def generate(self, order: dict[str, Any]) -> ChecklistResult:
        template_key = _required(order, "template_key")
        context = _context(order, str(template_key))

        output_mode = order.get("output_mode", "DRAFT")
        if output_mode == "APP_SETUP" and order.get("human_approval_status") != "APPROVED":
            raise ChecklistError(
                f"APP_SETUP 模式必须先完成人工审核: "
                f"human_approval_status != APPROVED; {context}"
            )

        if template_key == "daily_pct_change":
            return self._generate_daily_pct_change(order)

        if template_key == "grid_trading":
            return self._generate_grid_trading(order)

        raise ChecklistError(
            f"当前 MVP 不支持模板: {template_key}。"
            f"只支持 daily_pct_change / grid_trading；{context}"
        )

    def _generate_daily_pct_change(self, order: dict[str, Any]) -> ChecklistResult:
        template_key = "daily_pct_change"
        context = _context(order, template_key)

        template = self.templates.get(template_key)
        if not isinstance(template, dict):
            raise ChecklistError(
                f"broker_capabilities.yaml 缺少模板配置: {template_key}; {context}"
            )

        if not template.get("mvp_allowed", False):
            raise ChecklistError(f"日涨跌幅模板未开放 MVP 使用；{context}")

        security_code = _required(order, "security_code", template_key=template_key)

        side_raw = _required(order, "side", template_key=template_key)
        if side_raw not in ("buy", "sell"):
            raise ChecklistError(f"side 只能是 buy/sell，当前: {side_raw!r}; {context}")
        side: Side = side_raw

        pct_type = _required(order, "pct_type", template_key=template_key)
        if pct_type not in ("涨幅", "跌幅"):
            raise ChecklistError(f"pct_type 只能是 涨幅/跌幅，当前: {pct_type!r}; {context}")

        pct_value = _to_positive_decimal(
            _required(order, "pct_value", template_key=template_key),
            "pct_value",
            context=context,
        )

        max_daily_pct_value = _to_positive_decimal(
            order.get("max_daily_pct_value", DEFAULT_MAX_DAILY_PCT_VALUE),
            "max_daily_pct_value",
            context=context,
        )

        if pct_value > max_daily_pct_value:
            raise ChecklistError(
                f"MVP阶段日涨跌幅阈值超过上限: "
                f"pct_value={pct_value}, "
                f"max_daily_pct_value={max_daily_pct_value}; {context}"
            )

        lot_size = _to_positive_int(order.get("lot_size", 100), "lot_size", context=context)
        order_qty = _to_positive_int(
            _required(order, "order_qty", template_key=template_key),
            "order_qty",
            context=context,
        )
        _validate_lot_aligned(order_qty, lot_size, "order_qty", context=context)

        monitor_start = _normalize_time(_required(order, "monitor_start", template_key=template_key))
        monitor_end = _normalize_time(_required(order, "monitor_end", template_key=template_key))

        duration_minutes = _window_duration_minutes(monitor_start, monitor_end)

        if duration_minutes <= 0:
            raise ChecklistError(
                f"监控开始时间必须早于结束时间: "
                f"{monitor_start} >= {monitor_end}; {context}"
            )

        min_monitor_minutes = _to_positive_int(
            order.get("min_monitor_minutes", DEFAULT_MIN_MONITOR_MINUTES),
            "min_monitor_minutes",
            context=context,
        )

        if duration_minutes < min_monitor_minutes:
            raise ChecklistError(
                f"监控窗口过短: "
                f"monitor_start={monitor_start}, "
                f"monitor_end={monitor_end}, "
                f"duration={duration_minutes}分钟, "
                f"min_monitor_minutes={min_monitor_minutes}; {context}"
            )

        if _is_app_full_day_window(monitor_start, monitor_end) and not order.get(
            "allow_full_day_monitoring", False
        ):
            raise ChecklistError(
                "当前监控时段等于 APP 默认全天 09:30-14:57。"
                "如确需全天，请在订单中显式设置 allow_full_day_monitoring=True；"
                f"{context}"
            )

        default_values = _get_default_values(template)
        order_side_display = "买入" if side == "buy" else "卖出"

        order_price, price_confirmation_status, price_warnings = _resolve_daily_order_price(
            template,
            side,
            context=context,
        )

        warnings: list[str] = []
        warnings.extend(price_warnings)

        if price_confirmation_status != "screenshot_confirmed":
            warnings.append(
                f"{order_side_display}方向的委托价格“{order_price}”"
                f"当前状态为 {price_confirmation_status}，人工必须在 APP 中确认。"
            )

        fields = [
            {
                "app_field": "代码",
                "value": security_code,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "日涨跌幅类型",
                "value": pct_type,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "涨幅/跌幅",
                "value": f"{_format_decimal(pct_value)}%",
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "有效期至",
                "value": default_values.get("validity_shortcut", "1日"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "委托方向",
                "value": order_side_display,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "委托方式",
                "value": default_values.get("order_method", "限价委托"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "委托价格",
                "value": order_price,
                "confirmation_status": price_confirmation_status,
            },
            {
                "app_field": "委托数量",
                "value": order_qty,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "触发后",
                "value": order.get(
                    "trigger_action",
                    default_values.get("trigger_action", "仅通知"),
                ),
                "confirmation_status": "internal_order_or_config_default",
            },
            {
                "app_field": "自动撤单",
                "value": "开启",
                "confirmation_status": "config_default",
            },
            {
                "app_field": "自动撤单时间",
                "value": f'{default_values.get("auto_cancel_seconds", 30)}秒',
                "confirmation_status": "screenshot_confirmed_or_config_default",
            },
            {
                "app_field": "监控时段",
                "value": "开启",
                "confirmation_status": "internal_window_required",
            },
            {
                "app_field": "周期时段",
                "value": default_values.get("monitor_weekdays", "周一 ~ 周五"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "开始时间",
                "value": monitor_start,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "结束时间",
                "value": monitor_end,
                "confirmation_status": "internal_order",
            },
        ]

        manual_confirm_items = list(template.get("manual_confirm_items", []))
        if not manual_confirm_items:
            manual_confirm_items = [
                "确认证券代码为场内ETF",
                "确认委托方向正确",
                "确认委托数量为最小交易单位整数倍",
                "确认监控时段来自系统清单，不使用APP默认全天",
                "确认设置前、创建成功后、收盘后均截图留痕",
            ]

        safety_notes = list(template.get("safety_notes", []))

        return ChecklistResult(
            template_key=template_key,
            template_name=template.get("display_name", "日涨跌幅"),
            app_entry_path=list(template.get("app_entry_path", [])),
            fields=fields,
            manual_confirm_items=manual_confirm_items,
            warnings=warnings + safety_notes,
            screenshots_required=self._screenshots_required(),
            context={
                "system_order_id": order.get("system_order_id"),
                "security_code": security_code,
                "template_key": template_key,
                "output_mode": order.get("output_mode", "DRAFT"),
            },
        )

    def _generate_grid_trading(self, order: dict[str, Any]) -> ChecklistResult:
        template_key = "grid_trading"
        context = _context(order, template_key)

        template = self.templates.get(template_key)
        if not isinstance(template, dict):
            raise ChecklistError(
                f"broker_capabilities.yaml 缺少模板配置: {template_key}; {context}"
            )

        if not template.get("mvp_allowed", False):
            raise ChecklistError(f"网格交易模板未开放 MVP 使用；{context}")

        require_strict_monitor_window = bool(order.get("require_strict_monitor_window", True))
        allow_grid_without_monitor_window = bool(order.get("allow_grid_without_monitor_window", False))

        if require_strict_monitor_window and not allow_grid_without_monitor_window:
            raise ChecklistError(
                "网格交易截图未确认独立监控时段字段，可能在有效期内持续监控。"
                "为遵守窗口纪律，默认禁止生成 APP_SETUP 清单。"
                "如本单明确允许网格按有效期持续监控，请显式设置 "
                "allow_grid_without_monitor_window=True，并保留人工审批记录；"
                f"{context}"
            )

        security_code = _required(order, "security_code", template_key=template_key)

        initial_base_price = _to_positive_decimal(
            _required(order, "initial_base_price", template_key=template_key),
            "initial_base_price",
            context=context,
        )

        tick_size = _to_positive_decimal(
            order.get("tick_size", "0.001"),
            "tick_size",
            context=context,
        )

        lot_size = _to_positive_int(order.get("lot_size", 100), "lot_size", context=context)

        grid_pct = _to_positive_decimal(order.get("grid_pct", "1.0"), "grid_pct", context=context)
        if grid_pct < Decimal("0.5") or grid_pct > Decimal("3.0"):
            raise ChecklistError(
                f"MVP阶段 grid_pct 建议限制在 0.5%~3.0%: "
                f"grid_pct={grid_pct}; {context}"
            )

        quantity_reference_price = _to_positive_decimal(
            order.get("quantity_reference_price", initial_base_price),
            "quantity_reference_price",
            context=context,
        )

        quantity_price_basis = str(
            order.get("quantity_price_basis", "initial_base_price")
        )

        order_qty = _resolve_order_qty(
            order,
            price=quantity_reference_price,
            context=context,
        )
        _validate_lot_aligned(order_qty, lot_size, "order_qty", context=context)

        estimated_single_order_amount = (
            Decimal(order_qty) * quantity_reference_price
        ).quantize(Decimal("0.01"))

        current_position_qty = _to_non_negative_int(
            _required(order, "current_position_qty", template_key=template_key),
            "current_position_qty",
            context=context,
        )
        _validate_lot_aligned(
            current_position_qty,
            lot_size,
            "current_position_qty",
            context=context,
        )

        price_range_pct = _to_positive_decimal(
            order.get("price_range_pct", "3.0"),
            "price_range_pct",
            context=context,
        )

        if price_range_pct > Decimal("5.0") and not order.get("allow_wide_price_range", False):
            raise ChecklistError(
                f"MVP阶段价格区间不允许超过上下5%: price_range_pct={price_range_pct}; "
                f"如确需更宽区间，请显式设置 allow_wide_price_range=True；{context}"
            )

        lower_price = _round_to_tick(
            initial_base_price * (Decimal("1") - price_range_pct / Decimal("100")),
            tick_size,
        )
        upper_price = _round_to_tick(
            initial_base_price * (Decimal("1") + price_range_pct / Decimal("100")),
            tick_size,
        )

        if lower_price <= 0:
            raise ChecklistError(
                f"价格区间下限必须大于0: lower_price={lower_price}; {context}"
            )

        if upper_price <= lower_price:
            raise ChecklistError(
                f"价格区间上限必须大于下限: "
                f"lower={lower_price}, upper={upper_price}; {context}"
            )

        allow_sell_to_zero = bool(order.get("allow_sell_to_zero", False))

        if allow_sell_to_zero:
            default_min_core_position_qty = 0
        else:
            default_min_core_position_qty = _floor_to_lot(
                max(order_qty, int(current_position_qty * 0.5)),
                lot_size,
            )
            default_min_core_position_qty = min(
                default_min_core_position_qty,
                current_position_qty,
            )

        min_core_position_qty = _to_non_negative_int(
            order.get("min_core_position_qty", default_min_core_position_qty),
            "min_core_position_qty",
            context=context,
        )
        _validate_lot_aligned(
            min_core_position_qty,
            lot_size,
            "min_core_position_qty",
            context=context,
        )

        if min_core_position_qty > current_position_qty:
            raise ChecklistError(
                f"最低底仓不能超过当前持仓: "
                f"min_core_position_qty={min_core_position_qty}, "
                f"current_position_qty={current_position_qty}; {context}"
            )

        max_sell_qty_by_core = current_position_qty - min_core_position_qty
        max_sell_qty = _to_non_negative_int(
            order.get("max_sell_qty", max_sell_qty_by_core),
            "max_sell_qty",
            context=context,
        )
        _validate_lot_aligned(max_sell_qty, lot_size, "max_sell_qty", context=context)

        if max_sell_qty > max_sell_qty_by_core:
            raise ChecklistError(
                f"max_sell_qty 会跌破最低持仓限制: "
                f"max_sell_qty={max_sell_qty}, "
                f"最低持仓={min_core_position_qty}, "
                f"当前持仓={current_position_qty}; {context}"
            )

        weekly_max_buy_batches = _to_non_negative_int(
            order.get("weekly_max_buy_batches", 2),
            "weekly_max_buy_batches",
            context=context,
        )

        weekly_used_buy_batches = _to_non_negative_int(
            order.get("weekly_used_buy_batches", 0),
            "weekly_used_buy_batches",
            context=context,
        )

        remaining_weekly_buy_batches = max(
            0,
            weekly_max_buy_batches - weekly_used_buy_batches,
        )

        if remaining_weekly_buy_batches <= 0:
            raise ChecklistError(
                f"本周买入次数已达上限: "
                f"weekly_max_buy_batches={weekly_max_buy_batches}, "
                f"weekly_used_buy_batches={weekly_used_buy_batches}; {context}"
            )

        max_buy_qty = _to_non_negative_int(
            order.get("max_buy_qty", order_qty * remaining_weekly_buy_batches),
            "max_buy_qty",
            context=context,
        )
        _validate_lot_aligned(max_buy_qty, lot_size, "max_buy_qty", context=context)

        risk_max_position_qty = order.get("risk_max_position_qty")
        if risk_max_position_qty is not None:
            risk_max_position_qty_int = _to_non_negative_int(
                risk_max_position_qty,
                "risk_max_position_qty",
                context=context,
            )
            if current_position_qty + max_buy_qty > risk_max_position_qty_int:
                raise ChecklistError(
                    f"网格最高持仓会超过风控上限: "
                    f"current_position_qty + max_buy_qty = "
                    f"{current_position_qty + max_buy_qty}, "
                    f"risk_max_position_qty={risk_max_position_qty_int}; {context}"
                )

        min_position_qty = current_position_qty - max_sell_qty
        max_position_qty = current_position_qty + max_buy_qty

        if min_position_qty < min_core_position_qty:
            raise ChecklistError(
                f"最低持仓低于底仓要求: "
                f"min_position_qty={min_position_qty}, "
                f"min_core_position_qty={min_core_position_qty}; {context}"
            )

        default_values = _get_default_values(template)

        fields = [
            {
                "app_field": "代码",
                "value": security_code,
                "confirmation_status": "internal_order",
            },
            {
                "app_field": "初始基准价",
                "value": _format_decimal(initial_base_price),
                "confirmation_status": "internal_order_or_market_snapshot",
            },
            {
                "app_field": "涨跌类型",
                "value": "按百分比",
                "confirmation_status": "mvp_default",
            },
            {
                "app_field": "每上涨...卖出",
                "value": f"{_format_decimal(grid_pct)}%",
                "confirmation_status": "system_calculated",
            },
            {
                "app_field": "每下跌...买入",
                "value": f"{_format_decimal(grid_pct)}%",
                "confirmation_status": "system_calculated",
            },
            {
                "app_field": "有效期至",
                "value": default_values.get("validity_shortcut", "1日"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "委托方式",
                "value": default_values.get("order_method", "限价委托"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "买入价格",
                "value": default_values.get("buy_price", "即时卖一价"),
                "confirmation_status": "screenshot_confirmed_or_config_default",
            },
            {
                "app_field": "卖出价格",
                "value": default_values.get("sell_price", "即时买一价"),
                "confirmation_status": "inferred_or_config_default",
            },
            {
                "app_field": "委托数量",
                "value": order_qty,
                "confirmation_status": "system_calculated_or_internal_order",
            },
            {
                "app_field": "数量计算参考价",
                "value": _format_decimal(quantity_reference_price),
                "confirmation_status": "system_calculated_context",
            },
            {
                "app_field": "数量计算口径",
                "value": quantity_price_basis,
                "confirmation_status": "system_calculated_context",
            },
            {
                "app_field": "单次参考金额",
                "value": str(estimated_single_order_amount),
                "confirmation_status": "system_calculated_context",
            },
            {
                "app_field": "委托失败或超限",
                "value": default_values.get("fail_action", "自动休眠"),
                "confirmation_status": "config_default",
            },
            {
                "app_field": "价格区间限制",
                "value": "开启",
                "confirmation_status": "risk_required",
            },
            {
                "app_field": "价格区间下限",
                "value": _format_decimal(lower_price),
                "confirmation_status": "system_calculated",
            },
            {
                "app_field": "价格区间上限",
                "value": _format_decimal(upper_price),
                "confirmation_status": "system_calculated",
            },
            {
                "app_field": "持仓数量限制",
                "value": "开启",
                "confirmation_status": "risk_required",
            },
            {
                "app_field": "最低持仓数量",
                "value": min_position_qty,
                "confirmation_status": "system_calculated",
            },
            {
                "app_field": "最高持仓数量",
                "value": max_position_qty,
                "confirmation_status": "system_calculated_weekly_buy_limit",
            },
            {
                "app_field": "倍数委托",
                "value": "开启，但单笔委托上限不得超过本次委托数量",
                "confirmation_status": "risk_required",
            },
            {
                "app_field": "单笔委托上限",
                "value": order_qty,
                "confirmation_status": "risk_required",
            },
        ]

        warnings = [
            "网格交易截图未显示独立监控时段字段，可能按有效期持续监控。",
            "本清单只有在人工明确允许网格不绑定内部窗口时才生成。",
            "MVP阶段网格交易建议只用1日有效期、小数量、价格区间限制、持仓数量限制。",
            f"允许卖到0: {allow_sell_to_zero}。若为 True，最低持仓数量可为0。",
            (
                f"每周最大买入次数: {weekly_max_buy_batches}，"
                f"本周已买入次数: {weekly_used_buy_batches}，"
                f"本次按剩余 {remaining_weekly_buy_batches} 次计算最高持仓。"
            ),
        ]

        if "order_qty" not in order or order.get("order_qty") in (None, ""):
            warnings.append(
                "委托数量由 项目总资金 × 单次买入比例 ÷ 数量计算参考价 自动计算，"
                "并向下取整到最小交易单位。"
                f"本次数量计算参考价={_format_decimal(quantity_reference_price)}，"
                f"口径={quantity_price_basis}。"
            )

        manual_confirm_items = list(template.get("manual_confirm_items", []))
        if not manual_confirm_items:
            manual_confirm_items = [
                "确认代码为场内ETF",
                "确认初始基准价与当前行情接近",
                "确认涨跌类型为按百分比",
                "确认有效期优先选择1日",
                "确认价格区间限制已开启且上下限已填写",
                "确认持仓数量限制已开启且最低/最高持仓数量已填写",
                "确认是否允许最低持仓数量为0",
                "确认最高持仓数量符合每周买入次数限制",
                "确认单笔委托上限未放大",
                "确认设置前、创建成功后、收盘后均截图留痕",
            ]

        safety_notes = list(template.get("safety_notes", []))

        return ChecklistResult(
            template_key=template_key,
            template_name=template.get("display_name", "网格交易"),
            app_entry_path=list(template.get("app_entry_path", [])),
            fields=fields,
            manual_confirm_items=manual_confirm_items,
            warnings=warnings + safety_notes,
            screenshots_required=self._screenshots_required(),
            context={
                "system_order_id": order.get("system_order_id"),
                "security_code": security_code,
                "template_key": template_key,
                "output_mode": order.get("output_mode", "DRAFT"),
                "allow_grid_without_monitor_window": allow_grid_without_monitor_window,
                "allow_sell_to_zero": allow_sell_to_zero,
                "order_qty_source": (
                    "manual_order_qty"
                    if order.get("order_qty") not in (None, "")
                    else "project_total_capital_batch_buy_pct"
                ),
                "quantity_reference_price": _format_decimal(quantity_reference_price),
                "quantity_price_basis": quantity_price_basis,
                "estimated_single_order_amount": str(estimated_single_order_amount),
                "weekly_max_buy_batches": weekly_max_buy_batches,
                "weekly_used_buy_batches": weekly_used_buy_batches,
                "remaining_weekly_buy_batches": remaining_weekly_buy_batches,
            },
        )

    def _screenshots_required(self) -> list[str]:
        screenshot_cfg = self.output_policy.get("screenshot_required", {})

        result = []

        if screenshot_cfg.get("before_submit", True):
            result.append("创建策略前截图")

        if screenshot_cfg.get("after_created", True):
            result.append("策略创建成功截图")

        if screenshot_cfg.get("after_trigger_or_end", True):
            result.append("触发、休眠、结束或收盘后状态截图")

        return result


_CONTEXT_LABELS = {
    "system_order_id": "系统订单号",
    "security_code": "证券代码",
    "template_key": "模板键",
    "output_mode": "输出模式",
    "allow_grid_without_monitor_window": "允许网格不绑定监控窗口",
    "allow_sell_to_zero": "允许卖到0",
    "order_qty_source": "委托数量来源",
    "quantity_reference_price": "数量计算参考价",
    "quantity_price_basis": "数量计算口径",
    "estimated_single_order_amount": "单次参考金额",
    "weekly_max_buy_batches": "每周最大买入次数",
    "weekly_used_buy_batches": "本周已买入次数",
    "remaining_weekly_buy_batches": "本周剩余买入次数",
}


def render_checklist_text(result: ChecklistResult) -> str:
    """把清单渲染成人工可读文本。"""

    lines: list[str] = []

    lines.append(f"模板：{result.template_name}")
    lines.append("")

    if result.context:
        lines.append("订单上下文：")
        for key, value in result.context.items():
            if value is None:
                continue
            label = _CONTEXT_LABELS.get(key, key)
            lines.append(f"- {label}：{value}")
        lines.append("")

    lines.append("APP入口路径：")
    if result.app_entry_path:
        lines.append(" → ".join(result.app_entry_path))
    else:
        lines.append("- 未配置，请人工从策略交易页面进入对应模板")
    lines.append("")

    lines.append("字段填写清单：")
    for item in result.fields:
        status = item.get("confirmation_status")
        if status:
            lines.append(f"- {item['app_field']}：{item['value']}（{status}）")
        else:
            lines.append(f"- {item['app_field']}：{item['value']}")
    lines.append("")

    lines.append("人工确认项：")
    for item in result.manual_confirm_items:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("风险提示：")
    for item in result.warnings:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("截图留痕要求：")
    for item in result.screenshots_required:
        lines.append(f"- {item}")

    return "\n".join(lines)