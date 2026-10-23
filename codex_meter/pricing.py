"""Offline per-request token estimates; never an account bill or balance.

Rates are currency units per million tokens. Custom entries are Standard-tier
prices, not FX conversions. The caller must supply one request at a time and
mark cumulative/uncertain usage with ``precise=False``.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
import json
from pathlib import Path
import sys


_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
_REFERENCE_PATH = _ROOT / "assets" / "reference-prices.json"
_REFERENCE_SOURCE = "API参考估算"
_CUSTOM_SOURCE = "自定义估算"
_PRICE_FIELDS = ("input", "cached", "cache_write", "output")
_LONG_DEFAULTS = {
    "long_context_threshold": None,
    "long_input_multiplier": 1.0,
    "long_output_multiplier": 1.0,
}


@dataclass(frozen=True)
class Quote:
    amount: Decimal | None
    currency: str
    source: str
    reason: str


def default_config() -> dict:
    """Return fresh, JSON-serializable defaults for the three pricing settings."""
    return {"custom_rates": {}, "price_multiplier": 1.0, "use_reference_prices": True}


def normalize_model(model: str) -> str:
    """Remove routing prefixes; only explicitly documented aliases are mapped.

    Dated snapshots, suffixes and partial model names are not guessed. Non-string
    or empty identifiers normalize to an empty, unpriceable identifier.
    """
    if not isinstance(model, str):
        return ""
    name = model.strip().rsplit("/", 1)[-1].strip().lower()
    return _REFERENCE_DATA["aliases"].get(name, name)


def _number(value, field: str) -> int | float:
    if (
        type(value) not in (int, float)
        or not Decimal(str(value)).is_finite()
        or value < 0
    ):
        raise ValueError(f"{field} 必须是非负有限 JSON 数字（不接受布尔值或字符串）。")
    return value


def validate_custom_rate(raw) -> dict:
    """Validate an explicit rate; raise ValueError rather than guess missing prices.

    All four prices and USD/CNY currency are required. Optional long-context
    settings default to no threshold and unit multipliers. Returns a new plain
    dict containing exactly the eight rate keys, suitable for JSON persistence.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("自定义单价必须是 JSON 对象。")
    allowed = set(_PRICE_FIELDS) | {"currency"} | set(_LONG_DEFAULTS)
    if set(raw) - allowed:
        raise ValueError("未知单价字段：" + ", ".join(str(key) for key in raw if key not in allowed))
    for field in (*_PRICE_FIELDS, "currency"):
        if field not in raw:
            raise ValueError(f"缺少必填单价字段：{field}。")
    result = {field: _number(raw[field], field) for field in _PRICE_FIELDS}
    if raw["currency"] not in ("USD", "CNY"):
        raise ValueError("currency 必须明确指定 USD 或 CNY；不自动换算币种。")
    result["currency"] = raw["currency"]
    threshold = raw.get("long_context_threshold")
    if threshold is not None and (type(threshold) is not int or threshold < 0):
        raise ValueError("long_context_threshold 必须为非负整数或 null。")
    result["long_context_threshold"] = threshold
    for field in ("long_input_multiplier", "long_output_multiplier"):
        result[field] = _number(raw.get(field, _LONG_DEFAULTS[field]), field)
    return result


def _load_reference_data() -> tuple[dict, str]:
    """Read only the bundled snapshot; never fetch prices or invent a fallback."""
    try:
        data = json.loads(_REFERENCE_PATH.read_text(encoding="utf-8-sig"))
        sections = ("models", "aliases", "service_tier_multipliers")
        if not isinstance(data, dict) or any(not isinstance(data.get(key), dict) for key in sections):
            raise ValueError("价格快照缺少 models / aliases / service_tier_multipliers 对象。")
        models = data["models"]
        for model, raw in models.items():
            if not model or model != model.strip().lower() or "/" in model:
                raise ValueError("参考模型标识必须是规范化名称。")
            models[model] = validate_custom_rate(raw)
        for alias, target in data["aliases"].items():
            if not alias or alias != alias.strip().lower() or "/" in alias or alias in models or target not in models:
                raise ValueError("参考模型别名无效或指向未核验模型。")
        tier_data = data["service_tier_multipliers"]
        if set(tier_data) != set(models):
            raise ValueError("参考服务档位与模型列表不匹配。")
        allowed_tiers = {"standard", "fast", "flex", "batch", "ultrafast"}
        for model, tiers in tier_data.items():
            if not isinstance(tiers, dict) or tiers.get("standard") != 1 or set(tiers) - allowed_tiers:
                raise ValueError(f"参考服务档位无效：{model}。")
            for tier, value in tiers.items():
                _number(value, f"{model}/{tier}")
        return data, ""
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        empty = {"models": {}, "aliases": {}, "service_tier_multipliers": {}}
        return empty, f"参考价格文件不可用（reference-prices.json）：{exc}"


_REFERENCE_DATA, _REFERENCE_PROBLEM = _load_reference_data()
reference_rates: dict[str, dict] = deepcopy(_REFERENCE_DATA["models"])


def _usage_problem(usage: Mapping[str, int]) -> str:
    if not isinstance(usage, Mapping):
        return "用量必须是包含 input_tokens 和 output_tokens 的计数映射。"
    for name in ("input_tokens", "output_tokens"):
        if name not in usage:
            return f"缺少必要计数：{name}。"
    for name, value in usage.items():
        if not isinstance(name, str) or type(value) is not int or value < 0:
            return f"计数 {name} 必须为非负整数（不接受布尔值或小数）。"
    cached = usage.get("cached_input_tokens", 0)
    written = usage.get("cache_write_input_tokens", 0)
    if cached + written > usage["input_tokens"]:
        return "缓存分区无效：cached_input_tokens + cache_write_input_tokens 超过 input_tokens。"
    if usage.get("reasoning_output_tokens", 0) > usage["output_tokens"]:
        return "reasoning_output_tokens 超过 output_tokens；推理必须已包含于输出。"
    if "total_tokens" in usage and usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        return "total_tokens 必须等于 input_tokens + output_tokens，不能重复加缓存或推理。"
    return ""


def _tier_name(service_tier: str | None) -> str:
    if service_tier is None:
        return "standard"
    if not isinstance(service_tier, str):
        return ""
    name = service_tier.strip().lower()
    return {"default": "standard", "priority": "fast"}.get(name, name)


class PriceBook:
    """Validated settings snapshot. Create a new book after saving UI changes.

    ``reference_rates`` is a defensive, JSON-serializable dict for UI defaults;
    editing it does not change quotes. Invalid configuration raises ValueError.
    Invalid or uncertain per-request usage returns an unpriced Quote instead.
    """

    reference_rates: dict[str, dict]

    def __init__(self, config: dict | None = None):
        if config is not None and not isinstance(config, Mapping):
            raise ValueError("价格设置必须是 JSON 对象。")
        settings = default_config()
        if config is not None:
            settings.update({key: config[key] for key in settings if key in config})
        self._price_multiplier = Decimal(str(_number(settings["price_multiplier"], "price_multiplier")))
        if type(settings["use_reference_prices"]) is not bool:
            raise ValueError("use_reference_prices 必须是布尔值。")
        self._use_reference_prices = settings["use_reference_prices"]
        if not isinstance(settings["custom_rates"], Mapping):
            raise ValueError("custom_rates 必须是模型到单价的 JSON 对象。")
        self._custom_rates = {}
        for raw_model, raw_rate in settings["custom_rates"].items():
            model = normalize_model(raw_model)
            if not model:
                raise ValueError("custom_rates 的模型名称不能为空。")
            if model in self._custom_rates:
                raise ValueError(f"custom_rates 模型规范化后重复：{model}。")
            self._custom_rates[model] = validate_custom_rate(raw_rate)
        settings["custom_rates"] = self._custom_rates
        self.config = deepcopy(settings)
        self._reference_rates = deepcopy(_REFERENCE_DATA["models"])
        self.reference_rates = deepcopy(self._reference_rates)

    def quote(
        self,
        model: str,
        usage: Mapping[str, int],
        service_tier: str | None = None,
        precise: bool = True,
    ) -> Quote:
        """Estimate one request without rounding for display or adding reasoning."""
        model = normalize_model(model)
        rate = self._custom_rates.get(model)
        source = _CUSTOM_SOURCE if rate is not None else _REFERENCE_SOURCE
        if rate is None and self._use_reference_prices:
            rate = self._reference_rates.get(model)
        if rate is None:
            reason = (
                f"未知模型或没有已核验的参考单价：{model or '(空)'}。"
                if self._use_reference_prices
                else "已禁用参考价格，且模型没有自定义单价。"
            )
            if self._use_reference_prices and _REFERENCE_PROBLEM:
                reason = _REFERENCE_PROBLEM
            return Quote(None, "", source, reason)
        if precise is not True:
            return Quote(None, rate["currency"], source, "用量不精确：无法按单次请求应用长上下文价格。")
        problem = _usage_problem(usage)
        if problem:
            return Quote(None, rate["currency"], source, problem)
        tier = _tier_name(service_tier)
        factors = _REFERENCE_DATA["service_tier_multipliers"].get(model, {"standard": 1})
        factor = factors.get(tier)
        if factor is None:
            return Quote(None, rate["currency"], source, f"未知或未核验的服务档位：{service_tier!r}。")

        cached = usage.get("cached_input_tokens", 0)
        written = usage.get("cache_write_input_tokens", 0)
        uncached = usage["input_tokens"] - cached - written
        prices = {name: Decimal(str(rate[name])) for name in _PRICE_FIELDS}
        input_multiplier = output_multiplier = Decimal(1)
        threshold = rate["long_context_threshold"]
        if threshold is not None and usage["input_tokens"] > threshold:
            input_multiplier = Decimal(str(rate["long_input_multiplier"]))
            output_multiplier = Decimal(str(rate["long_output_multiplier"]))
        tier_multiplier = Decimal(str(factor))
        numbers = (*prices.values(), input_multiplier, output_multiplier, tier_multiplier, self._price_multiplier)
        # Bit length bounds decimal digits; the exponent span protects tiny terms
        # when adding large ones. Never inherit a caller's display-rounding context.
        precision = max(
            28,
            max(usage["input_tokens"].bit_length(), usage["output_tokens"].bit_length())
            + sum(len(n.as_tuple().digits) + abs(n.as_tuple().exponent) for n in numbers)
            + 10,
        )
        with localcontext(Context(prec=precision)):
            input_cost = (
                uncached * prices["input"]
                + cached * prices["cached"]
                + written * prices["cache_write"]
            ) * input_multiplier
            output_cost = usage["output_tokens"] * prices["output"] * output_multiplier
            amount = (
                (input_cost + output_cost) * tier_multiplier * self._price_multiplier
                / Decimal(1_000_000)
            )
        return Quote(amount, rate["currency"], source, "")
