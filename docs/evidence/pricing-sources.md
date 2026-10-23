# Pricing implementation and source evidence

Verified on **2026-10-23**. This document covers only the independent pricing task.
The application uses an offline, token-only **API参考估算**, never an actual bill,
subscription debit, account balance, or third-party provider quote.

## Official sources fetched independently

Read-only requests fetched the complete official Markdown pages below; the HTML
pages were also opened through web lookup. No authenticated API requests were made.

- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra.md)
- [GPT-6.1 Sol model](https://developers.openai.com/api/docs/models/gpt-6.1-sol.md)
- [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol.md)
- [API pricing tables](https://developers.openai.com/api/docs/pricing.md)
- [Fast mode](https://developers.openai.com/api/docs/guides/fast-mode.md)
- [Ultrafast mode](https://developers.openai.com/api/docs/guides/ultrafast-mode.md)

The model pages establish base rates and model-specific qualifications. The pricing
page independently supplies the Standard, Batch, Flex, Fast, and Ultrafast tables,
including both cache-write retention durations. The two mode guides establish
service-tier names and limitations. Only the three requested models are bundled;
a model being unconfigured here does not imply that no official price exists.

## Frozen reference rates

**USD per 1,000,000 tokens**, Standard tier, request input at most 272,000 tokens:

| Model | Uncached input | Cache read | Cache write, 30m | Output |
| --- | ---: | ---: | ---: | ---: |
| `gpt-6-astra` | 10 | 1 | 12.5 | 50 |
| `gpt-6.1-sol` | 2 | 0.1 | 2.5 | 10 |
| `gpt-5.6-sol` | 4 | 0.4 | 5 | 20 |

The GPT-5.6 Sol model page supplies its cache-write price as 1.25 times uncached
input; the pricing table also explicitly lists USD 5. All other displayed base
prices are directly listed in the model pages. GPT-6.1 Sol cached input is 0.1,
not the 0.2 listed for the separate GPT-6 Sol model.

For **one request with input strictly greater than 272,000 tokens**, all three
models use 2 times each input/cache rate and 1.5 times output for the entire
request. This is not marginal pricing above the threshold. The GPT-5.6 Sol pricing
table explicitly confirms the cache-read and cache-write long-context rates even
though its model-page wording is shorter. Exactly 272,000 remains short context.

Verified multipliers relative to the applicable short/long Standard rates:

| Model | Standard | Fast | Flex | Batch | Ultrafast |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gpt-6-astra` | 1 | 2 | 0.5 | 0.5 | 6 |
| `gpt-6.1-sol` | 1 | 2 | 0.5 | 0.5 | 6 |
| `gpt-5.6-sol` | 1 | 2 | 0.5 | 0.5 | Unconfigured |

Fast mode accepts `priority` as an alias. The Fast guide says GPT-5.6 and earlier
responses may report `priority` even when the request used `fast`; a downgraded
request can report `default`. Callers should pass the **resolved response tier**
when available, rather than assume the originally requested tier was delivered.

## Important limits of these estimates

- The single `cache_write` rate represents **30-minute retention**. The pricing
  table separately lists 12-hour write prices of USD 25 / 5 / 10 for Astra / 6.1
  Sol / 5.6 Sol, before long-context and tier factors. The current flat usage
  contract cannot distinguish retention durations or a mixture of them. Configure
  an appropriate custom rate for a known uniform duration, or mark uncertain
  requests `precise=False`; never silently treat a known 12-hour write as 30m.
- The GPT-5.6 Sol model page states the promotional price is available **at least
  through November 21, 2026**. That is not a confirmed expiry date. No later price
  is invented, and the offline snapshot does not automatically switch prices.
- The pricing page lists 10% uplifts for regional processing and FedRAMP endpoints.
  These conditions cannot be inferred from model/token counts. This implementation
  does not automatically add them. No tool-call/container/storage fees are added.
- An absent tier (`None`) means the **Standard reference assumption**, not a claim
  about an account's project default. `auto`, `scale`, arbitrary strings, and
  unsupported model/tier pairs remain unpriced. GPT-5.6 Sol Ultrafast is not guessed.
- A routing prefix is not proof that a third party bills at OpenAI's rates. The
  source label remains `API参考估算` for any reference-rate calculation, including
  a non-unit configured channel multiplier.
- This is a dated snapshot, not historical billing reconstruction. Quotes have no
  timestamp input. Actual historical or provider-specific prices require explicit
  user configuration. There is no network refresh, exchange-rate lookup, currency
  conversion, credential access, or guessed replacement model.

## Public API and integration contract

```python
normalize_model(model: str) -> str
default_config() -> dict
validate_custom_rate(raw) -> dict
PriceBook(config: dict | None = None)
PriceBook.quote(
    model: str,
    usage: Mapping[str, int],
    service_tier: str | None = None,
    precise: bool = True,
) -> Quote
```

`Quote` is a frozen dataclass with exactly these fields:
`amount: Decimal | None`, `currency: str`, `source: str`, `reason: str`.
A priced quote has an empty `reason`; an unpriced quote explains the failure.
Unknown rates have an empty currency rather than an invented USD/CNY rate.
A known rate retains its currency/source when its usage or tier is unpriceable.

Fresh default pricing settings:

```json
{"custom_rates": {}, "price_multiplier": 1.0, "use_reference_prices": true}
```

- `PriceBook().reference_rates` is a plain `dict[normalized_model, dict]` with
  exactly `input`, `cached`, `cache_write`, `output`, `currency`,
  `long_context_threshold`, `long_input_multiplier`, `long_output_multiplier`.
  It remains available for UI defaults when reference pricing is disabled. Both
  it and the module-level `reference_rates` are defensive copies, not live pricing
  controls. Construct a new `PriceBook` after saving settings.
- Full main/UI settings dictionaries are accepted; nonpricing top-level keys are
  ignored. Configurations are copied rather than retained by reference.
- `validate_custom_rate` requires all four explicit prices and either `USD` or
  `CNY`. Prices/multipliers must be finite, nonnegative JSON integers or floats;
  booleans, numeric strings, Decimal objects, NaN, and infinities are rejected.
  The UI must parse entry text to JSON numbers before validation.
- Optional long-context fields default to `None`, `1.0`, `1.0`. A threshold must
  be a nonnegative integer or `None`; its boundary is strict. Zero prices and
  multipliers are valid. Validation returns a new, JSON-serializable eight-key
  dictionary. Missing required fields and unknown/misspelled rate fields raise
  `ValueError`; no missing cache price or currency is guessed.
- Invalid pricing configuration also raises `ValueError` in `PriceBook`; do not
  silently discard a bad custom row and substitute a plausible reference price.
  UI/settings load paths should report the validation error to the user.
- Custom entries override reference rates and use source `自定义估算`, preserving
  the declared currency. They are Standard-tier prices. For a configured official
  model, verified tier factors still apply. Unknown custom models support only
  Standard (`None`, `default`, `standard`); no Fast/Flex/etc. factor is invented.
  Custom long-context rules are independent and do not inherit reference rules.
- Normalization strips whitespace, lowercases, and removes slash-separated route
  prefixes. The one verified explicit alias is `gpt-5.6` -> `gpt-5.6-sol`.
  Suffixes, dates, partial names, and similar model names are not guessed.
  Duplicate custom keys after normalization raise `ValueError`.

## Usage and arithmetic

Required counters are `input_tokens` and `output_tokens`. Optional cache and
reasoning counters default to zero. Every supplied counter must be a nonnegative
Python `int` (not `bool`, float, string, null, NaN, or infinity).

```text
uncached = input_tokens - cached_input_tokens - cache_write_input_tokens
input_cost = uncached*input_rate + cached*cached_rate + written*write_rate
output_cost = output_tokens*output_rate
amount = (input_cost*long_input_factor + output_cost*long_output_factor)
         * service_tier_factor * price_multiplier / 1_000_000
```

`cached + written > input` is invalid; equality is valid. Reasoning must not exceed
output, and is **not added** to it. Optional `total_tokens` must equal input plus
output. Invalid counts, unknown/unresolved tiers, unknown models, or
`precise=False` yield `amount=None`, not a fabricated zero. Valid zero usage with
a known rate can yield `Decimal(0)`.

The book does not accumulate token counts or keep a context-window running total.
Call `quote` separately for each request. A cumulative/baseline amount must not be
passed as precise request usage. Calculation uses Decimal representations of JSON
numbers and its own sufficiently large local Decimal context, retaining tiny
components and avoiding caller display precision. There is no display rounding.

Required worked example (Astra, Standard):

```text
input=1000, cached=200, written=300, output=100, reasoning=80
uncached=500
(500*10 + 200*1 + 300*12.5 + 100*50) / 1_000_000
= Decimal("0.01395") USD
```

## Runtime resource behavior

The snapshot is read once, at module import, from:

1. `sys._MEIPASS/assets/reference-prices.json` for a bundled application;
2. otherwise `Path(__file__).resolve().parents[1]/assets/reference-prices.json`.

The resource is not resolved from the working directory. Missing, malformed, or
invalid reference data disables the entire reference snapshot and explains why
in unpriced quotes; it does not prevent valid custom Standard rates from working.
There is no online fallback. Package the JSON under the bundle's `assets` directory.
Tests simulate `_MEIPASS` resolution; this independent task did not build an EXE.

## RED/GREEN evidence

Interpreter: `D:/python3.12/python.exe` (Python 3.12.2). Test runner: standard-library
`unittest`; pytest is neither installed nor required. `-B` prevents bytecode writes
outside the four-file assignment.

| Stage | Observed RED | Observed GREEN |
| --- | --- | --- |
| Required Astra example | 1 test, 1 failure: pricing module absent | Same test passes, amount exactly 0.01395 |
| Normalization, partitions, tiers, context, precision flags | 31 test methods, 112 failing cases/subtests | Included in the 34-test GREEN below |
| UI defensive rates and bundle path | 3 focused tests, 2 failures: wrong bundle path and mutable live defaults | 34 pricing tests pass |
| Custom currencies/config validation | 56 test methods, 123 failing cases/subtests | 56 pricing tests pass |
| Missing/corrupt resources and Decimal precision | 6 focused tests, 10 failing cases/subtests | 62 pricing tests pass |

Final regression commands run from the project directory:

```powershell
& 'D:\python3.12\python.exe' -B -m unittest tests.test_pricing -v
# Ran 62 tests; OK

& 'D:\python3.12\python.exe' -B -m unittest tests.test_pricing tests.test_presentation -v
# Ran 70 tests; OK (62 pricing + 8 main-worker presentation integration tests)
```

An additional in-memory independent arithmetic check compared **2,500 quotes**
against Python `fractions.Fraction` with deterministic seed `20261023`: 100
requests for each of 23 reference model/tier combinations, plus 100 randomized
custom cases in each of USD and CNY. It included exact threshold boundaries,
cache partitions, output/reasoning subsets, long multipliers, and channel factors.
All 2,500 matched exactly. `PriceBook(default_settings())` was also checked in
memory against main's settings API. No settings or log files were written.

All implementation/test/document changes are confined to the four assigned files.
Main owns full collector/native/UI packaging verification. No other project files,
Codex logs, credentials, startup settings, Git worktrees, or branches were edited.
