"""Offline, deterministic tests for per-request token price estimates."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, is_dataclass
from decimal import Decimal, localcontext
import json
import runpy
import sys
from pathlib import Path
from types import MappingProxyType
import unittest
from unittest.mock import patch

from codex_meter import pricing


def usage(**changes):
    """One request: output already includes reasoning, input includes both caches."""
    counters = {
        "input_tokens": 1000,
        "cached_input_tokens": 200,
        "cache_write_input_tokens": 300,
        "output_tokens": 100,
        "reasoning_output_tokens": 80,
    }
    counters.update(changes)
    return counters


class PricingTestCase(unittest.TestCase):
    def assert_unpriced(self, book, model="gpt-6-astra", counters=None, **options):
        try:
            quote = book.quote(model, usage() if counters is None else counters, **options)
        except Exception as exc:
            self.fail(f"Invalid/uncertain data must return an unpriced Quote, not {exc!r}")
        self.assertIsNone(quote.amount)
        self.assertTrue(quote.reason.strip(), "Unpriced quotes must explain why")
        return quote


class ReferencePricingTests(PricingTestCase):
    def test_astra_partitions_cache_and_does_not_double_count_reasoning(self):
        quote = pricing.PriceBook().quote("gpt-6-astra", usage(total_tokens=1100))
        self.assertEqual(quote.amount, Decimal("0.01395"))
        self.assertEqual(quote.currency, "USD")
        self.assertEqual(quote.source, "API参考估算")
        self.assertIsInstance(quote.reason, str)
        self.assertTrue(is_dataclass(quote))
        with self.assertRaises(FrozenInstanceError):
            quote.amount = Decimal(0)

    def test_verified_sol_rates(self):
        for model, expected in (
            ("gpt-6.1-sol", "0.00277"),
            ("gpt-5.6-sol", "0.00558"),
        ):
            with self.subTest(model=model):
                quote = pricing.PriceBook().quote(model, usage())
                self.assertEqual(quote.amount, Decimal(expected))
                self.assertEqual(quote.currency, "USD")
                self.assertEqual(quote.source, "API参考估算")

    def test_snapshot_provenance_and_exact_supported_models(self):
        path = Path(__file__).resolve().parents[1] / "assets" / "reference-prices.json"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["verified_on"], "2026-10-23")
        self.assertEqual(data["source"], "API参考估算")
        self.assertEqual(data["cache_write_retention"], "30m")
        self.assertEqual(
            set(data["models"]), {"gpt-6-astra", "gpt-6.1-sol", "gpt-5.6-sol"}
        )
        self.assertEqual(data["models"], pricing.reference_rates)
        for model in data["models"]:
            self.assertTrue(data["sources"][model].startswith("https://developers.openai.com/"))

    def test_defaults_are_json_serializable_and_independent(self):
        expected = {"custom_rates": {}, "price_multiplier": 1.0, "use_reference_prices": True}
        config = pricing.default_config()
        self.assertEqual(config, expected)
        self.assertEqual(json.loads(json.dumps(config)), expected)
        config["custom_rates"]["new-model"] = {"input": 0}
        self.assertEqual(pricing.default_config(), expected)

    def test_zero_known_usage_is_zero(self):
        for model in pricing.reference_rates:
            with self.subTest(model=model):
                quote = pricing.PriceBook().quote(model, {"input_tokens": 0, "output_tokens": 0})
                self.assertEqual(quote.amount, Decimal(0))

    def test_missing_optional_cache_and_reasoning_counters_mean_zero(self):
        quote = pricing.PriceBook().quote("gpt-6-astra", {"input_tokens": 1000, "output_tokens": 100})
        self.assertEqual(quote.amount, Decimal("0.015"))

    def test_no_display_rounding_of_small_amounts(self):
        quote = pricing.PriceBook().quote(
            "gpt-6.1-sol", {"input_tokens": 1, "cached_input_tokens": 1, "output_tokens": 0}
        )
        self.assertEqual(quote.amount, Decimal("0.0000001"))

    def test_reference_switch_disables_fallback(self):
        quote = self.assert_unpriced(pricing.PriceBook({"use_reference_prices": False}))
        self.assertEqual(quote.currency, "")
        self.assertEqual(quote.source, "API参考估算")

    def test_multiplier_is_applied_once_without_changing_currency(self):
        for factor, expected in ((0, "0"), (0.1, "0.001395"), (2, "0.0279")):
            with self.subTest(factor=factor):
                quote = pricing.PriceBook({"price_multiplier": factor}).quote("gpt-6-astra", usage())
                self.assertEqual(quote.amount, Decimal(expected))
                self.assertEqual(quote.currency, "USD")
                self.assertEqual(quote.source, "API参考估算")

    def test_unknown_model_is_not_priced_even_for_zero_usage(self):
        for model in (
            "gpt-6-sol", "gpt-6.1-sol-pro", "gpt-6-astra-2026-10-23",
            "gpt-6-astra-latest", "gpt-5.6-sol-preview", "not-a-model", "", None, 7,
        ):
            with self.subTest(model=model):
                self.assert_unpriced(
                    pricing.PriceBook(), model, {"input_tokens": 0, "output_tokens": 0}
                )


class ModelNormalizationTests(PricingTestCase):
    def test_normalizes_route_prefixes_without_fuzzy_matching(self):
        for raw, expected in (
            ("gpt-6-astra", "gpt-6-astra"),
            (" ccs-openai/GPT-6.1-SOL ", "gpt-6.1-sol"),
            ("ccs-docode-key/gpt-5.6-sol", "gpt-5.6-sol"),
            ("providers/openai/gpt-6-astra", "gpt-6-astra"),
            ("gpt-6-astra-pro", "gpt-6-astra-pro"),
            ("gpt-6.1-sol-2026-10-23", "gpt-6.1-sol-2026-10-23"),
            ("gpt-6-astra:fast", "gpt-6-astra:fast"),
            (" ccS-aiPM/gpt-5.6 ", "gpt-5.6-sol"),
            ("route/", ""),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(pricing.normalize_model(raw), expected)

    def test_quoting_uses_normalized_model(self):
        quote = pricing.PriceBook().quote("ccs-openai/GPT-6-ASTRA", usage())
        self.assertEqual(quote.amount, Decimal("0.01395"))

    def test_only_documented_alias_is_priced(self):
        self.assertEqual(
            pricing.PriceBook().quote("gpt-5.6", usage()).amount, Decimal("0.00558")
        )
        self.assert_unpriced(pricing.PriceBook(), "gpt-6.1")
        self.assert_unpriced(pricing.PriceBook(), "gpt-6")


class UsageValidationTests(PricingTestCase):
    def test_rejects_missing_required_counters(self):
        for counters in ({}, {"input_tokens": 0}, {"output_tokens": 0}, {"input": 100, "output": 1}):
            with self.subTest(counters=counters):
                quote = self.assert_unpriced(pricing.PriceBook(), counters=counters)
                self.assertIn("tokens", quote.reason)

    def test_rejects_non_mapping_usage_without_raising(self):
        for counters in (None, [], "usage", 123):
            with self.subTest(counters=counters):
                try:
                    quote = pricing.PriceBook().quote("gpt-6-astra", counters)
                except Exception as exc:
                    self.fail(f"Invalid usage must return an unpriced Quote, not {exc!r}")
                self.assertIsNone(quote.amount)
                self.assertTrue(quote.reason)

    def test_rejects_negative_noninteger_and_nonfinite_counters(self):
        invalid = (-1, 1.5, 1.0, True, False, "1", None, float("nan"), float("inf"), Decimal(1))
        for field in usage():
            for value in invalid:
                with self.subTest(field=field, value=value):
                    quote = self.assert_unpriced(pricing.PriceBook(), counters=usage(**{field: value}))
                    self.assertIn(field, quote.reason)

    def test_rejects_invalid_cached_input_partitions(self):
        for cached, written in ((1001, 0), (0, 1001), (800, 201)):
            with self.subTest(cached=cached, written=written):
                quote = self.assert_unpriced(
                    pricing.PriceBook(), counters=usage(cached_input_tokens=cached, cache_write_input_tokens=written)
                )
                self.assertIn("input_tokens", quote.reason)

    def test_all_input_can_be_cache_partitioned(self):
        quote = pricing.PriceBook().quote("gpt-6-astra", usage(cache_write_input_tokens=800))
        self.assertEqual(quote.amount, Decimal("0.0152"))

    def test_reasoning_output_is_a_subset_not_an_additional_charge(self):
        book = pricing.PriceBook()
        for reasoning in (0, 1, 80, 100):
            with self.subTest(reasoning=reasoning):
                self.assertEqual(
                    book.quote("gpt-6-astra", usage(reasoning_output_tokens=reasoning)).amount,
                    Decimal("0.01395"),
                )
        self.assert_unpriced(book, counters=usage(reasoning_output_tokens=101))

    def test_rejects_invalid_or_inconsistent_total(self):
        for value in (-1, 1100.0, True, "1100", float("nan"), 1099, 1180):
            with self.subTest(value=value):
                quote = self.assert_unpriced(pricing.PriceBook(), counters=usage(total_tokens=value))
                self.assertIn("total_tokens", quote.reason)

    def test_imprecise_or_cumulative_usage_stays_unpriced_even_if_zero(self):
        for counters in (usage(), {"input_tokens": 0, "output_tokens": 0}):
            with self.subTest(counters=counters):
                quote = self.assert_unpriced(pricing.PriceBook(), counters=counters, precise=False)
                self.assertEqual(quote.currency, "USD")
                self.assertEqual(quote.source, "API参考估算")

    def test_supports_read_only_mappings_without_mutation(self):
        original = usage()
        view = MappingProxyType(original)
        self.assertIsInstance(view, Mapping)
        self.assertEqual(pricing.PriceBook().quote("gpt-6-astra", view).amount, Decimal("0.01395"))
        self.assertEqual(original, usage())


class ContextAndTierTests(PricingTestCase):
    def test_long_context_boundary_is_strict_and_reprices_whole_request(self):
        for count, expected in ((271999, "2.56999"), (272000, "2.57"), (272001, "5.13752")):
            with self.subTest(count=count):
                counters = usage(input_tokens=count, cached_input_tokens=20000, cache_write_input_tokens=10000)
                self.assertEqual(pricing.PriceBook().quote("gpt-6-astra", counters).amount, Decimal(expected))

    def test_long_context_includes_cached_input_and_cache_write(self):
        counters = usage(input_tokens=272001, cached_input_tokens=200000, cache_write_input_tokens=72001)
        self.assertEqual(
            pricing.PriceBook().quote("gpt-6-astra", counters).amount, Decimal("2.207525")
        )

    def test_output_does_not_activate_long_input_threshold(self):
        counters = {"input_tokens": 100000, "output_tokens": 200000}
        self.assertEqual(pricing.PriceBook().quote("gpt-6-astra", counters).amount, Decimal("11"))

    def test_independent_requests_never_accumulate_for_context_threshold(self):
        book = pricing.PriceBook()
        counters = {"input_tokens": 150000, "output_tokens": 0}
        quotes = [book.quote("gpt-6-astra", counters) for _ in range(3)]
        self.assertEqual([q.amount for q in quotes], [Decimal("1.5")] * 3)
        self.assertEqual(sum(q.amount for q in quotes), Decimal("4.5"))
        book.quote("gpt-6-astra", {"input_tokens": 800000, "output_tokens": 0})
        self.assertEqual(book.quote("gpt-6-astra", counters).amount, Decimal("1.5"))

    def test_verified_tier_factors_and_documented_aliases(self):
        tiers = ((None, 1), ("standard", 1), ("default", 1), ("fast", 2), ("priority", 2), (" FaSt ", 2), ("flex", 0.5), ("batch", 0.5))
        for model, base in (("gpt-6-astra", "0.01395"), ("gpt-6.1-sol", "0.00277"), ("gpt-5.6-sol", "0.00558")):
            for tier, factor in tiers:
                with self.subTest(model=model, tier=tier):
                    quote = pricing.PriceBook().quote(model, usage(), service_tier=tier)
                    self.assertEqual(quote.amount, Decimal(base) * Decimal(str(factor)))

    def test_ultrafast_is_only_available_for_verified_models(self):
        for model, expected in (("gpt-6-astra", "0.0837"), ("gpt-6.1-sol", "0.01662")):
            with self.subTest(model=model):
                self.assertEqual(
                    pricing.PriceBook().quote(model, usage(), "ultrafast").amount, Decimal(expected)
                )
        self.assert_unpriced(pricing.PriceBook(), "gpt-5.6-sol", service_tier="ultrafast")

    def test_unknown_or_unresolved_tiers_are_not_standard(self):
        for tier in ("auto", "scale", "unknown", "", 7, [], "fast-ish"):
            with self.subTest(tier=tier):
                self.assert_unpriced(pricing.PriceBook(), service_tier=tier)

    def test_context_tier_and_channel_multipliers_compose(self):
        counters = usage(input_tokens=272001, cached_input_tokens=20000, cache_write_input_tokens=10000)
        quote = pricing.PriceBook({"price_multiplier": 0.1}).quote("gpt-6-astra", counters, "fast")
        self.assertEqual(quote.amount, Decimal("1.027504"))

    def test_sol_long_context_cache_rates_are_verified_too(self):
        counters = usage(input_tokens=272001, cached_input_tokens=20000, cache_write_input_tokens=10000)
        for model, expected in (("gpt-6.1-sol", "1.023504"), ("gpt-5.6-sol", "2.055008")):
            with self.subTest(model=model):
                self.assertEqual(pricing.PriceBook().quote(model, counters).amount, Decimal(expected))


class ResourceAndUIContractTests(PricingTestCase):
    def test_packaged_snapshot_uses_meipass_assets(self):
        source = Path(pricing.__file__).resolve()
        snapshot = (source.parents[1] / "assets" / "reference-prices.json").read_text(encoding="utf-8-sig")
        bundle = source.parents[1] / "not-an-actual-bundle-directory"
        expected = bundle / "assets" / "reference-prices.json"
        paths = []

        def read_snapshot(path, *args, **kwargs):
            paths.append(path)
            return snapshot

        with patch.object(sys, "_MEIPASS", str(bundle), create=True), patch.object(Path, "read_text", read_snapshot):
            isolated = runpy.run_path(str(source))
            quote = isolated["PriceBook"]().quote("gpt-6-astra", usage())
        self.assertEqual(quote.amount, Decimal("0.01395"))
        self.assertIn(expected, paths)

    def test_ui_rates_are_plain_dicts_with_exact_config_keys(self):
        expected = {"input", "cached", "cache_write", "output", "currency", "long_context_threshold", "long_input_multiplier", "long_output_multiplier"}
        rates = pricing.PriceBook().reference_rates
        self.assertIsInstance(rates, dict)
        for model, rate in rates.items():
            self.assertEqual(pricing.normalize_model(model), model)
            self.assertIsInstance(rate, dict)
            self.assertEqual(set(rate), expected)
        json.dumps(rates, allow_nan=False)

    def test_ui_reference_edits_cannot_mutate_live_pricing(self):
        book = pricing.PriceBook()
        view = book.reference_rates
        previous = view["gpt-6-astra"]["input"]
        try:
            view["gpt-6-astra"]["input"] = 999
            self.assertEqual(book.quote("gpt-6-astra", usage()).amount, Decimal("0.01395"))
            self.assertEqual(pricing.PriceBook().quote("gpt-6-astra", usage()).amount, Decimal("0.01395"))
        finally:
            view["gpt-6-astra"]["input"] = previous


def custom_rate(**changes):
    rate = {"input": 2, "cached": 0.5, "cache_write": 3, "output": 10, "currency": "CNY"}
    rate.update(changes)
    return rate


class CustomPricingTests(PricingTestCase):
    def test_custom_currency_overrides_reference_without_conversion(self):
        book = pricing.PriceBook({"custom_rates": {"gpt-6-astra": custom_rate()}})
        quote = book.quote("gpt-6-astra", usage())
        self.assertEqual(quote.amount, Decimal("0.003"))
        self.assertEqual(quote.currency, "CNY")
        self.assertEqual(quote.source, "自定义估算")
        self.assertEqual(book.reference_rates["gpt-6-astra"]["currency"], "USD")

    def test_custom_model_works_with_reference_prices_disabled(self):
        for currency in ("CNY", "USD"):
            with self.subTest(currency=currency):
                book = pricing.PriceBook({"custom_rates": {"vendor/my-model": custom_rate(currency=currency)}, "use_reference_prices": False, "price_multiplier": 1.5})
                quote = book.quote("another-route/my-model", usage())
                self.assertEqual(quote.amount, Decimal("0.0045"))
                self.assertEqual(quote.currency, currency)
                self.assertEqual(quote.source, "自定义估算")

    def test_custom_official_model_uses_verified_tier_factors(self):
        book = pricing.PriceBook({"custom_rates": {"ccs-openai/GPT-6-ASTRA": custom_rate()}})
        for tier, expected in (("default", "0.003"), ("priority", "0.006"), ("flex", "0.0015"), ("ultrafast", "0.018")):
            with self.subTest(tier=tier):
                quote = book.quote("gpt-6-astra", usage(), tier)
                self.assertEqual(quote.amount, Decimal(expected))
                self.assertEqual(quote.currency, "CNY")
                self.assertEqual(quote.source, "自定义估算")

    def test_custom_unknown_model_does_not_invent_tier_factors(self):
        book = pricing.PriceBook({"custom_rates": {"my-model": custom_rate()}})
        for tier in (None, "default", "standard"):
            self.assertEqual(book.quote("my-model", usage(), tier).amount, Decimal("0.003"))
        for tier in ("fast", "flex", "batch", "ultrafast", "auto"):
            with self.subTest(tier=tier):
                quote = self.assert_unpriced(book, "my-model", service_tier=tier)
                self.assertEqual(quote.source, "自定义估算")
                self.assertEqual(quote.currency, "CNY")

    def test_custom_long_context_defaults_do_not_inherit_reference_steps(self):
        book = pricing.PriceBook({"custom_rates": {"gpt-6-astra": custom_rate()}})
        quote = book.quote("gpt-6-astra", {"input_tokens": 300000, "output_tokens": 100})
        self.assertEqual(quote.amount, Decimal("0.601"))

    def test_custom_long_context_threshold_and_multipliers(self):
        rate = custom_rate(long_context_threshold=1000, long_input_multiplier=2.5, long_output_multiplier=0.75)
        book = pricing.PriceBook({"custom_rates": {"my-model": rate}})
        self.assertEqual(book.quote("my-model", usage()).amount, Decimal("0.003"))
        self.assertEqual(book.quote("my-model", usage(input_tokens=1001)).amount, Decimal("0.005755"))

    def test_zero_threshold_only_applies_to_positive_input(self):
        rate = custom_rate(long_context_threshold=0, long_input_multiplier=2, long_output_multiplier=3)
        book = pricing.PriceBook({"custom_rates": {"my-model": rate}})
        self.assertEqual(book.quote("my-model", {"input_tokens": 0, "output_tokens": 100}).amount, Decimal("0.001"))
        self.assertEqual(book.quote("my-model", {"input_tokens": 1, "output_tokens": 100}).amount, Decimal("0.003004"))

    def test_zero_custom_rates_are_valid(self):
        rate = custom_rate(input=0, cached=0, cache_write=0, output=0)
        quote = pricing.PriceBook({"custom_rates": {"my-model": rate}}).quote("my-model", usage())
        self.assertEqual(quote.amount, Decimal(0))
        self.assertEqual(quote.currency, "CNY")
        self.assertEqual(quote.source, "自定义估算")

    def test_decimal_arithmetic_does_not_import_binary_float_error(self):
        rate = custom_rate(input=0.1, cached=0.01, cache_write=0.125, output=0.2)
        quote = pricing.PriceBook({"custom_rates": {"my-model": rate}}).quote("my-model", {"input_tokens": 3, "output_tokens": 7})
        self.assertEqual(quote.amount, Decimal("0.0000017"))

    def test_custom_imprecise_quotes_preserve_source_and_currency(self):
        book = pricing.PriceBook({"custom_rates": {"my-model": custom_rate()}})
        for options in ({"precise": False}, {"service_tier": "unknown"}):
            quote = self.assert_unpriced(book, "my-model", **options)
            self.assertEqual(quote.currency, "CNY")
            self.assertEqual(quote.source, "自定义估算")
        quote = self.assert_unpriced(book, "my-model", counters=usage(cached_input_tokens=1001))
        self.assertEqual(quote.currency, "CNY")

    def test_configuration_is_not_mutated_or_retained_by_reference(self):
        config = {"custom_rates": {"my-model": custom_rate()}, "price_multiplier": 2}
        original = json.loads(json.dumps(config))
        book = pricing.PriceBook(config)
        self.assertEqual(config, original)
        config["custom_rates"]["my-model"]["input"] = 999
        config["price_multiplier"] = 50
        self.assertEqual(book.quote("my-model", usage()).amount, Decimal("0.006"))


class RateValidationTests(PricingTestCase):
    def validate(self, raw):
        validator = getattr(pricing, "validate_custom_rate", None)
        self.assertTrue(callable(validator), "Expose validate_custom_rate(raw) for the settings UI")
        return validator(raw)

    def test_validator_expands_optional_defaults_as_json_numbers(self):
        raw = custom_rate()
        rate = self.validate(raw)
        self.assertEqual(rate, {**raw, "long_context_threshold": None, "long_input_multiplier": 1.0, "long_output_multiplier": 1.0})
        json.dumps(rate, allow_nan=False)
        rate["input"] = 99
        self.assertEqual(raw, custom_rate())

    def test_validator_accepts_complete_reference_rates(self):
        for rate in pricing.PriceBook().reference_rates.values():
            self.assertEqual(self.validate(rate), rate)

    def test_validator_rejects_negative_nonfinite_or_non_number_rates(self):
        for field in ("input", "cached", "cache_write", "output", "long_input_multiplier", "long_output_multiplier"):
            for value in (-1, float("nan"), float("inf"), float("-inf"), True, False, "2", None, [], Decimal("2")):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        self.validate(custom_rate(**{field: value}))

    def test_validator_requires_each_explicit_price_and_currency(self):
        for field in ("input", "cached", "cache_write", "output", "currency"):
            with self.subTest(field=field):
                raw = custom_rate()
                del raw[field]
                with self.assertRaisesRegex(ValueError, field):
                    self.validate(raw)

    def test_validator_rejects_invalid_currency_without_guessing(self):
        for currency in ("EUR", "usd", "RMB", "", None, 1):
            with self.subTest(currency=currency):
                with self.assertRaisesRegex(ValueError, "currency"):
                    self.validate(custom_rate(currency=currency))

    def test_validator_requires_nonnegative_integer_or_null_threshold(self):
        for threshold in (-1, 1000.0, True, "272000", float("nan"), float("inf"), []):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, "long_context_threshold"):
                    self.validate(custom_rate(long_context_threshold=threshold))
        for threshold in (None, 0, 1000):
            self.assertEqual(self.validate(custom_rate(long_context_threshold=threshold))["long_context_threshold"], threshold)

    def test_validator_rejects_nonobjects_and_misspelled_fields(self):
        for raw in (None, [], "2", {}, custom_rate(cahced=0.5)):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.validate(raw)

    def test_book_rejects_invalid_custom_rate_without_reference_fallback(self):
        for rate in (custom_rate(input=-1), custom_rate(output=float("nan")), {}, None):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    pricing.PriceBook({"custom_rates": {"gpt-6-astra": rate}})

    def test_book_rejects_invalid_global_settings(self):
        for config in ([], "settings", {"custom_rates": []}, {"custom_rates": None}, {"use_reference_prices": "false"}, {"use_reference_prices": 1}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    pricing.PriceBook(config)
        for value in (-1, float("nan"), float("inf"), True, "1", None):
            with self.subTest(multiplier=value):
                with self.assertRaisesRegex(ValueError, "price_multiplier"):
                    pricing.PriceBook({"price_multiplier": value})

    def test_book_rejects_empty_model_keys_and_normalized_duplicates(self):
        for model in ("", "   ", "route/", None, 7):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    pricing.PriceBook({"custom_rates": {model: custom_rate()}})
        for rates in (
            {"gpt-6-astra": custom_rate(), "ccs-openai/GPT-6-ASTRA": custom_rate()},
            {"gpt-5.6": custom_rate(), "gpt-5.6-sol": custom_rate()},
        ):
            with self.subTest(rates=rates):
                with self.assertRaises(ValueError):
                    pricing.PriceBook({"custom_rates": rates})

    def test_book_accepts_whole_ui_settings_and_ignores_nonpricing_keys(self):
        config = {**pricing.default_config(), "top_inset": 64, "expanded": True, "schema_version": 1}
        self.assertEqual(pricing.PriceBook(config).quote("gpt-6-astra", usage()).amount, Decimal("0.01395"))


class OfflineAndPrecisionTests(PricingTestCase):
    def test_ambient_decimal_context_cannot_round_or_overflow_a_quote(self):
        with localcontext() as context:
            context.prec = 3
            context.Emax = 2
            try:
                quote = pricing.PriceBook().quote("gpt-6-astra", usage())
            except Exception as exc:
                self.fail(f"Caller Decimal context must not affect pricing: {exc!r}")
            self.assertEqual(context.prec, 3)
            self.assertEqual(context.Emax, 2)
        self.assertEqual(quote.amount, Decimal("0.01395"))

    def test_finite_rates_of_different_magnitudes_keep_small_component(self):
        rate = custom_rate(input=1e30, cached=0, cache_write=0, output=1e-30)
        book = pricing.PriceBook({"custom_rates": {"my-model": rate}})
        quote = book.quote("my-model", {"input_tokens": 1, "output_tokens": 1})
        expected = Decimal("1" + "0" * 24 + "." + "0" * 35 + "1")
        self.assertEqual(quote.amount, expected)

    def test_large_integer_counters_do_not_lose_low_digits(self):
        count = 10 ** 36 + 1
        quote = pricing.PriceBook().quote("gpt-6-astra", {"input_tokens": count, "output_tokens": 0})
        self.assertEqual(quote.amount, Decimal(f"{20 * count}e-6"))

    def test_missing_reference_asset_fails_closed_but_custom_rates_work(self):
        with patch.object(Path, "read_text", side_effect=FileNotFoundError("reference asset missing")):
            try:
                isolated = runpy.run_path(pricing.__file__)
            except Exception as exc:
                self.fail(f"Missing prices must yield an unpriced Quote, not an import failure: {exc!r}")
        book = isolated["PriceBook"]()
        quote = self.assert_unpriced(book)
        self.assertIn("reference-prices.json", quote.reason)
        self.assertEqual(book.reference_rates, {})
        custom = isolated["PriceBook"]({"custom_rates": {"my-model": custom_rate()}})
        self.assertEqual(custom.quote("my-model", usage()).amount, Decimal("0.003"))
        self.assert_unpriced(custom, "my-model", service_tier="fast")

    def test_malformed_reference_assets_do_not_become_plausible_prices(self):
        path = Path(pricing.__file__).resolve().parents[1] / "assets" / "reference-prices.json"
        original = json.loads(path.read_text(encoding="utf-8-sig"))
        bad_rate = json.loads(json.dumps(original))
        bad_rate["models"]["gpt-6-astra"]["input"] = float("nan")
        bad_tier = json.loads(json.dumps(original))
        bad_tier["service_tier_multipliers"]["gpt-6-astra"]["fast"] = -1
        bad_alias = json.loads(json.dumps(original))
        bad_alias["aliases"]["gpt-5.6"] = "nonexistent-model"
        for text in ("not-json", "[]", "{}", json.dumps(bad_rate), json.dumps(bad_tier), json.dumps(bad_alias)):
            with self.subTest(text=text[:80]):
                with patch.object(Path, "read_text", return_value=text):
                    try:
                        isolated = runpy.run_path(pricing.__file__)
                        book = isolated["PriceBook"]()
                    except Exception as exc:
                        self.fail(f"Malformed reference data must fail closed: {exc!r}")
                quote = self.assert_unpriced(book)
                self.assertIn("reference-prices.json", quote.reason)

    def test_module_import_and_quote_require_no_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Runtime must remain offline")):
            isolated = runpy.run_path(pricing.__file__)
            book = isolated["PriceBook"]()
            self.assertEqual(book.quote("gpt-6-astra", usage()).amount, Decimal("0.01395"))


if __name__ == "__main__":
    unittest.main()
