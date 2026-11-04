"""The laptop check of the streamed seed (the bootstrap's streaming plan, 2026-11-04): on the
seeded 5% sample of the real warehouse (trials with hash(nct_id || ':bootstrap-sample-
2026-11-04') % 20 = 0), the stream gives what `seed` gives on the same versions read at
once, and its sponsor file is the file of one `advance` call over every state. The
reference reads the versions with a query of its own (every column, the full texts table,
no order) and converts them with the project's `compact_row`. Slow, and skipped without the
warehouse in data/."""

import datetime as dt
from itertools import groupby

import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.live import bootstrap, contract
from trialpulse.live.seed import seed, seed_trial
from trialpulse.live.sponsors import QUEUED, SponsorFile

WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"
SAMPLE = "hash(nct_id || ':bootstrap-sample-2026-11-04') % 20 = 0"
SECRET = b"a test key for the sponsor ids"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not WAREHOUSE.is_file(), reason="needs the warehouse in data/"),
]


def test_the_streamed_seed_equals_seed_on_the_seeded_sample() -> None:
    cfg = load_project_config()
    assert cfg.dataset.cutoff is not None
    day = cfg.dataset.cutoff
    rules = CohortRules.from_config(cfg, cutoff=day)
    aliases = (cfg.features.sponsor_alias_min_trials, cfg.features.sponsor_alias_wait_days)
    keep = cfg.live.recent_post_days
    with bootstrap.open_warehouse(WAREHOUSE) as con:
        con.execute(f"CREATE TEMP TABLE sample AS SELECT nct_id FROM trials WHERE {SAMPLE}")
        streamed = bootstrap.stream_seed(con, day, rules, SECRET, keep, *aliases,
                                         trials="sample")  # fmt: skip
        cursor = con.execute(
            """SELECT v.*, ot.text AS official_title_text, bt.text AS brief_title_text
            FROM versions v JOIN sample USING (nct_id)
            LEFT JOIN texts ot ON ot.text_hash = v.official_title_hash
            LEFT JOIN texts bt ON bt.text_hash = v.brief_title_hash"""
        )
        names = [d[0] for d in cursor.description]
        rows = []
        for record in cursor.fetchall():
            v = dict(zip(names, record, strict=True))
            row = contract.compact_row(v, v["official_title_text"], v["brief_title_text"],
                                       v["intervention_types"], rules, SECRET)  # fmt: skip
            row["nct_version"] = v["nct_version"]
            rows.append(row)
    expected = seed(rows, day, rules, keep, *aliases)
    assert streamed.counts["trials"] == 30_087  # as measured on 2026-11-04
    assert streamed.counts["versions"] == len(rows) == 224_871
    assert streamed.wides == expected.wides
    assert streamed.markers == expected.markers
    assert streamed.sponsors.to_bytes() == expected.sponsors.to_bytes()

    ordered = sorted(
        (r for r in rows if r["effective_date"] <= day),
        key=lambda v: (v["nct_id"], v["effective_date"], v["nct_version"]),
    )
    states = []
    for _, group in groupby(ordered, key=lambda v: v["nct_id"]):
        states += [{c: s.get(c) for c in QUEUED}
                   for s in seed_trial(list(group), day, rules, keep).states]  # fmt: skip
    single = SponsorFile.empty(*aliases, dt.date.min)
    single.advance(states, day, rules)
    assert streamed.sponsors.to_bytes() == single.to_bytes()
