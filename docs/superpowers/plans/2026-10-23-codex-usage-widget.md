# Codex Usage Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a read-only, no-focus Windows usage bar attached to the Codex desktop window.

**Architecture:** Python standard-library Tk UI, ctypes Win32 attachment, incremental JSONL usage collector, and decimal per-request price engine. Independent pricing and Win32 adapters have disjoint write scopes; the main worker owns ingestion, settings, UI, integration and packaging.

**Tech Stack:** Python 3.12, tkinter 8.6, ctypes, unittest, PyInstaller for Windows delivery.

## Global Constraints
- Runtime is offline; never read auth.json or write Codex session/config files.
- No automatic startup registration; single instance; no global always-on-top over unrelated apps.
- UTC+8 day boundary. Every amount is an estimate, never a charge or balance.
- Native attachment supports observed ChatGPT.exe and Codex.exe processes.
- New project E:/codex_wj/codex-usage-widget is isolated from existing projects; workspace is not a Git repository, so do not create worktrees or commits in unrelated projects.

## Task 1: Incremental collector (main worker)
Files: codex_meter/usage.py, tests/test_usage.py.
Interface: UsageCollector(home).poll(now=None) -> UsageSnapshot; snapshot.sessions, snapshot.today_events, snapshot.errors. Session records expose id, model, provider, latest_activity, events; events expose timestamp, usage, model, service_tier, precise.
- [ ] Write unittest fixtures with complete session_meta / turn_context / token_count records for duplicated totals, cached tokens, partial lines, archived copies, reset counters, UTC date boundaries and forked history.
- [ ] Run `D:/python3.12/python.exe -m unittest tests.test_usage -v` and record RED.
- [ ] Parse only supported record types and retain byte offsets plus incomplete line tails. Use cumulative differences; a repeated cumulative tuple creates no event. Rebuild a cursor on truncation/replacement. Merge same-session events by stable identity.
- [ ] Record baseline amounts with timestamp=None when first cumulative is larger than last request; exclude baseline from today and mark its price uncertain.
- [ ] Run the same suite GREEN; independently compare today counters with local event sums.

## Task 2: Decimal pricing (independent worker)
Files: codex_meter/pricing.py, tests/test_pricing.py, assets/reference-prices.json, docs/evidence/pricing-sources.md.
Interface: PriceBook(config=None).quote(model, usage, service_tier=None, precise=True) -> Quote(amount Decimal|None, currency, source, reason); normalize_model(model); default_config().
- [ ] Fetch official model markdown for GPT-6 Astra, GPT-6.1 Sol and GPT-5.6 Sol; record exact rate snapshots and caveats.
- [ ] Write failing unittest cases, including `input=1000, cached=200, write=300, output=100` at Astra rates => Decimal('0.01395'). Do not add reasoning outputs twice.
- [ ] Implement exact model matching (route-prefix stripping allowed), input/cache partition, unknown handling, long-request and service-tier multipliers, validated custom rates and currency.
- [ ] Test zero, negative, nonfinite, unknown tiers, mixed/missing usage and precision warnings; record GREEN.

## Task 3: Native attachment (independent worker)
Files: codex_meter/native.py, tests/test_native.py.
Interface: Rect(left,top,right,bottom); WindowInfo(hwnd,pid,title,rect,visible,minimized); NativeWindows.list_hosts(), foreground_window(), root_window(hwnd), rect(hwnd), is_minimized(hwnd), is_window(hwnd), configure_overlay(hwnd), show_overlay(hwnd,x,y,width,height), hide(hwnd); anchor_position(host_rect,size,offset,work_area) pure function.
- [ ] Write failing tests for clamping and attachment visibility policy.
- [ ] Implement ctypes prototypes with pointer-safe handles, exact executable matching and visible unowned top-level host detection. No window title alone identifies a host.
- [ ] Set WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW, show using SW_SHOWNOACTIVATE and SetWindowPos SWP_NOACTIVATE; no focus forcing.
- [ ] Validate with real Win32 windows and keep calls import-safe on non-Windows tests.

## Task 4: Configuration and UI (main worker)
Files: codex_meter/settings.py, codex_meter/presentation.py, codex_meter/app.py, main.py, tests/test_settings.py, tests/test_presentation.py, tools/smoke_ui.py.
- [ ] Write failing tests for atomic settings, invalid file fallback, currency formatting, unknown/partial quote display, active main-session selection and fixed positioning.
- [ ] Implement no-secret settings in LOCALAPPDATA/CodexUsageWidget/settings.json with per-user instance guard.
- [ ] Implement dark compact Tk strip, expandable breakdown, user-activated settings dialog, session locking, right-click menu, draggable relative offsets and singleton re-show.
- [ ] Run collector in a background worker, deliver immutable snapshots to Tk queue; native window follower updates at 250ms and collector at 2s.
- [ ] Add diagnostic `--snapshot` JSON mode and UI test target/exit controls so packaging and behavior can be checked without manipulating the real Codex window.
- [ ] Run full unittest suite plus dummy-host UI smoke script; inspect cropped widget screenshot and adjust overlaps.

## Task 5: Delivery and verification (main worker)
Files: tools/build.ps1, Launch.vbs, Start.cmd, README.md, docs/evidence/verification.json.
- [ ] Build in project-local packaging environment; produce dist/CodexUsageWidget.exe with bundled rates and an app icon.
- [ ] Run full tests, packaged --snapshot, native smoke, and duplicate-launch checks; record observed outputs and limitations.
- [ ] Create a desktop shortcut only for this delivered tool and start the executable hidden (the widget itself is user-requested UI).
- [ ] Final response links executable and source directory, describes settings and estimate caveat, reports verified behavior.
