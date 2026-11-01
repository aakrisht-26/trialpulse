"""The live tables' store: a rebuild clears every table the daily job writes (review of
Steps 14 to 16, finding 4)."""

import re
from pathlib import Path

from trialpulse.config import REPO_ROOT
from trialpulse.live import store

# The modules whose SQL the daily job runs (its own, the store's, the display rows' and the
# serving tables' publication).
WRITERS = ("live/daily.py", "live/store.py", "live/display.py", "scoring/serving.py")
KEPT = {"monitoring.pipeline_runs", "serving.data_status"}  # cleared by row and by column


def _written(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM|TRUNCATE)\s+((?:live|serving|"
                       r"monitoring)\.[a-z_]+)", text)  # fmt: skip
    return set(found)


def test_a_rebuild_clears_every_table_the_daily_job_writes() -> None:
    written: set[str] = set()
    for name in WRITERS:
        written |= _written(REPO_ROOT / "src" / "trialpulse" / name)
    assert written  # the scan found the daily job's tables
    assert written - KEPT <= set(store.REBUILT_TABLES)
    assert set(store.REBUILT_TABLES) <= written  # and clears nothing it does not write
