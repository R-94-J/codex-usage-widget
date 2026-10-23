# Final scoped code review

Reviewed collector-to-pricing integration, configuration recovery, stable session selection, and native UI orchestration.

Six important edge cases were reproduced and fixed with test-first regressions:
1. Reject replayed cumulative records before changing the checkpoint.
2. Reconcile last-only requests when cumulative reporting returns.
3. Exclude unknown inherited fork baselines.
4. Validate price semantics and normalized keys inside settings recovery, preserving corrupt originals.
5. Preserve old/missing pinned sessions outside the recent-session dropdown limit.
6. Reconcile reset-era last-only usage against baseline only, preserving the known new last request; flag ambiguous overlap.

The reviewer verified all original five reproductions resolved and the two reset combinations returned 130 and 150 tokens respectively. No remaining important issues were identified within that bounded verification scope. Native foreground behavior and packaged UI were separately tested by the main worker; see verification.json.
