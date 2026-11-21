"""scripts/check_dashboard.py against stubbed pages: what passes, what fails, a sleeping app
woken, and a page that never finishes. No browser is started."""

import importlib.util
from collections.abc import Sequence

from trialpulse.config import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "check_dashboard", REPO_ROOT / "scripts" / "check_dashboard.py"
)
assert spec is not None
assert spec.loader is not None
check_dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_dashboard)

FOOTER = (
    "Source: ClinicalTrials.gov. TrialPulse normalizes the registry records. "
    "Data as of 2026-11-09 (the date ClinicalTrials.gov processed the data)."
)
GOOD = (
    f"Model health\n{check_dashboard.NOTICE}\nFreshness\n"
    f"Registered results of the locked test\n{FOOTER}"
)
CHECK = check_dashboard.Check("Model health", "/model-health",
                              ("Freshness", "Registered results of the locked test"))  # fmt: skip


class Stub:
    """A page whose text moves through `texts` on each read, with error boxes counted."""

    def __init__(self, texts: Sequence[str], errors: int = 0, wake: bool = False,
                 labels: Sequence[str] = ()) -> None:  # fmt: skip
        self.texts, self.reads, self.errors, self.wake = list(texts), 0, errors, wake
        self.opened: list[str] = []
        self.clicked = 0
        self.labels = list(labels)
        self.expanded: list[str] = []

    def open(self, url: str) -> None:
        self.opened.append(url)

    def text(self) -> str:
        text = self.texts[min(self.reads, len(self.texts) - 1)]
        self.reads += 1
        return text

    def count(self, selector: str) -> int:
        return self.errors if "Error" in selector else 0

    def click_text(self, text: str) -> bool:
        if self.wake and text == check_dashboard.WAKE:
            self.clicked += 1
            return True
        return False

    def click_label(self, text: str) -> bool:
        if text in self.labels:
            self.expanded.append(text)
            return True
        return False


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _check(stub: Stub, check: object = CHECK, budget: float = 240.0) -> list[str]:
    clock = Clock()
    return check_dashboard.check_page(stub, "https://app.test/", check, budget,
                                      clock.now, clock.sleep)  # fmt: skip


def test_a_page_with_the_frame_and_its_content_passes() -> None:
    stub = Stub([GOOD])
    assert _check(stub) == []
    assert stub.opened == ["https://app.test/model-health"]


def test_a_sleeping_app_is_woken_and_then_checked() -> None:
    stub = Stub(["Zzzz. This app has gone to sleep.", "Zzzz", GOOD], wake=True)
    assert _check(stub) == []
    assert stub.clicked >= 1


def test_every_missing_part_of_the_frame_fails() -> None:
    page = "Model health\nFreshness\nSource: ClinicalTrials.gov. Data as of: not known yet."
    failures = _check(Stub([page]))
    assert "no notice" in failures
    assert "no data-as-of date (the API did not answer)" in failures
    assert "missing: Registered results of the locked test" in failures


def test_an_error_box_or_a_traceback_fails() -> None:
    assert any("error box" in f for f in _check(Stub([GOOD], errors=1)))
    assert "a traceback" in _check(Stub([GOOD + "\nTraceback (most recent call last):"]))


def test_a_page_that_never_finishes_fails_within_the_budget() -> None:
    failures = _check(Stub(["Waiting for the API to start."]), budget=60.0)
    assert failures == ["the page did not finish within 60 s"]


def test_a_page_without_api_calls_needs_no_date() -> None:
    portfolio = next(c for c in check_dashboard.pages() if c.name == "Portfolio")
    text = f"{check_dashboard.NOTICE}\nSponsor name starts with\nSource: ClinicalTrials.gov."
    assert _check(Stub([text]), portfolio) == []


def test_trial_detail_is_checked_for_a_score_and_for_the_not_found_message() -> None:
    names = {c.name: c for c in check_dashboard.pages("NCT06447662")}
    assert names["Trial detail (scored)"].path == "/trial?nct=NCT06447662"
    missing = names["Trial detail (not served)"]
    assert missing.path == "/trial?nct=NCT00000000"
    assert "TrialPulse serves no trial with the id NCT00000000" in missing.expect
    assert len(check_dashboard.pages()) == 8  # with the memo section (ADR 0085)


MEMO_CHECK = next(c for c in check_dashboard.pages() if c.name == "Trial detail (memo)")
OPENED = (
    f"Trial detail\n{check_dashboard.NOTICE}\n{check_dashboard.MEMO_LABEL}\nFirst paragraph."
    f"\nBuilt on 2026-11-21 by the template (memo_v6). Every paragraph ... "
    f"{check_dashboard.MEMO_NOTE} (ADR 0085).\n{check_dashboard.NOTICE}\n{FOOTER}"
)


def test_the_memo_section_is_opened_by_its_label_and_checked() -> None:
    """ADR 0085: the memo trial's page shows the section's label, its note and its own copy of
    the notice once the section is opened."""
    assert MEMO_CHECK.path == f"/trial?nct={check_dashboard.MEMO_ID}"
    stub = Stub([OPENED], labels=[check_dashboard.MEMO_LABEL])
    assert _check(stub, MEMO_CHECK) == []
    assert stub.expanded == [check_dashboard.MEMO_LABEL]


def test_a_page_without_the_memo_section_fails() -> None:
    assert _check(Stub([OPENED]), MEMO_CHECK) == [
        f"no section labeled {check_dashboard.MEMO_LABEL!r}"
    ]
    one_notice = OPENED.replace(f"(ADR 0085).\n{check_dashboard.NOTICE}", "(ADR 0085).")
    failures = _check(Stub([one_notice], labels=[check_dashboard.MEMO_LABEL]), MEMO_CHECK)
    assert failures == ["the notice 1 times, not 2"]
