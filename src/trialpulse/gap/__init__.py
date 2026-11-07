"""The gap read of ADR 0041: laptop only, before go-live.

The pinned revision ends on its cutoff; go-live comes later. These modules read what came
after, from two sources kept apart under data/gap/ and written to no live table:

- `gap.pull`: the real API v2 pull from the cutoff minus the overlap, as the daily job pulls.
- `gap.release`: the newer release of the dataset at a pinned commit (the card, the files).
- `gap.adapter`: the release's layout mapped to the pinned columns (ADR 0073).
- `gap.compare`: the overlap check against the pinned warehouse, and the gap store.
- `gap.parity`: parity layer 5b of ADR 0038, the API pull against the gap rows.
- `gap.rebootstrap`: the live state seeded from pinned plus gap rows, and its reference
  check (ADR 0074).

Every module refuses under GitHub Actions (ADR 0040), and no row of either source enters a
commit, a log or a test fixture.
"""
