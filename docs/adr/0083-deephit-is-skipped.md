# 0083. Stretch item 1, DeepHit, is skipped

- Date: 2026-11-17
- Status: **Accepted.** Decided by Aakrisht on 2026-11-17.

## Context

CLAUDE.md's Stretch list names DeepHit (pycox) as an extra challenger inside the same walk-forward harness, to be built after Step 18 and before the risk memo (Stretch item 2).

## Decision

DeepHit is not built. Stretch item 2 (the per-trial risk memo) is next in its place, after its own planning.

Reasons:

1. **Cost.** It adds PyTorch (and pycox) to the locked stack (Section 15) and a long training job on a laptop with no GPU, against the rule that a job over about an hour needs a cheaper plan first.
2. **Expected gain.** The features are tabular: 54 point-in-time features of design, amendments, sponsor, competition and time (ADR 0026). On such data a neural survival model rarely beats a tuned gradient-boosted model, and the discrete-time LightGBM already handles the competing event directly. A gain is unlikely.
3. **No fair test left.** The locked test (origins 2018, 2019 and 2020) has been run once and is spent (ADRs 0004, 0009, 0031). Any comparison now would use the development origins 2016 and 2017 only, so it could inform a later change but never become a registered result.

## Alternatives

- **Build it as specified:** rejected for the three reasons above.
- **A cheaper neural baseline** (a multinomial multilayer perceptron on the person-period rows, in scikit-learn): not built either; it shares reasons 2 and 3.

## Consequences

- PyTorch and pycox stay out of the dependencies.
- The model ladder ends at M4 (ADRs 0024 to 0026), and CLAUDE.md's Stretch item 1 is recorded as skipped in its Amendments.
