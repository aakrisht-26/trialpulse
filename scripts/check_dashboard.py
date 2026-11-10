"""Check a deployed TrialPulse dashboard page by page (Step 16; docs/deploy.md, Part 2).

    uv run python scripts/check_dashboard.py --url https://<app>.streamlit.app
    uv run python scripts/check_dashboard.py --url http://localhost:8501

With Playwright (the installed Chrome, a fresh profile, headless), it opens every page of the
app and waits for each to finish, within the dashboard's own waking budget (ADR 0054: about 3
minutes) plus a margin; a Community Cloud app that sleeps is woken with its own button. A page
passes when it shows the notice, the source attribution with a data-as-of date (ADR 0008),
its own content, and no error box or traceback. Trial detail must show a scored trial's score
card, and the plain not-found message for NCT00000000. It prints one line per page and exits
1 on any failure. It reads pages only; it types nothing but a registry id in the address.
"""

import argparse
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

NOTICE = "Research demo. Not medical advice. Not for patient decision-making."
SOURCE = "Source: ClinicalTrials.gov."
DATA_AS_OF = re.compile(r"Data as of \d{4}-\d{2}-\d{2}")
ERRORS = ('[data-testid="stAlertContentError"]', '[data-testid="stException"]')
WAKE = "get this app back up"  # Community Cloud's button on a sleeping app
BUDGET_SECONDS = 240.0  # the dashboard's 3-minute waking budget, plus a margin
SCORED_ID = "NCT06447662"  # scored, not flagged, on 2026-11-10
MISSING_ID = "NCT00000000"


class Probe(Protocol):
    """What the check needs of a browser page (Playwright's, or a stub in the tests)."""

    def open(self, url: str) -> None: ...
    def text(self) -> str: ...
    def count(self, selector: str) -> int: ...
    def click_text(self, text: str) -> bool: ...


@dataclass(frozen=True)
class Check:
    name: str
    path: str
    expect: tuple[str, ...]  # text the page must show besides the frame
    needs_date: bool = True


def pages(scored: str = SCORED_ID) -> list[Check]:
    return [
        Check("Overview", "/", ("Trials with a score",)),
        Check("Portfolio", "/portfolio", ("Sponsor name starts with",), needs_date=False),
        Check(
            "Trial detail (scored)",
            f"/trial?nct={scored}",
            ("Early-stop risk within 12 months", "Top drivers", "Timeline"),
        ),
        Check(
            "Trial detail (not served)",
            f"/trial?nct={MISSING_ID}",
            (f"TrialPulse serves no trial with the id {MISSING_ID}",),
        ),
        Check("Landscape", "/landscape", ("distinct open trials in all",)),
        Check("Why trials stop", "/why-trials-stop", ("free-text reasons sponsors gave",)),
        Check(
            "Model health", "/model-health", ("Freshness", "Registered results of the locked test")
        ),
    ]


def wait_finished(probe: Probe, budget: float, now: Callable[[], float],
                  sleep: Callable[[float], None]) -> bool:  # fmt: skip
    """Wait until the page's footer (drawn last, also after an error) shows; wake a sleeping
    app. True if it showed within the budget."""
    end = now() + budget
    while now() < end:
        if SOURCE in probe.text():
            sleep(1.0)  # the last elements of the same run
            return True
        probe.click_text(WAKE)
        sleep(2.0)
    return False


def check_page(probe: Probe, base: str, check: Check, budget: float = BUDGET_SECONDS,
               now: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep) -> list[str]:  # fmt: skip
    """The failures of one page (empty when it passes)."""
    probe.open(base.rstrip("/") + check.path)
    if not wait_finished(probe, budget, now, sleep):
        return [f"the page did not finish within {budget:.0f} s"]
    text = probe.text()
    failures = []
    if NOTICE not in text:
        failures.append("no notice")
    if SOURCE not in text:
        failures.append("no source attribution")
    if check.needs_date and not DATA_AS_OF.search(text):
        failures.append("no data-as-of date (the API did not answer)")
    if "Traceback" in text:
        failures.append("a traceback")
    for selector in ERRORS:
        if probe.count(selector):
            failures.append(f"an error box ({selector})")
    failures += [f"missing: {want}" for want in check.expect if want not in text]
    return failures


class PlaywrightProbe:
    def __init__(self, page: object) -> None:
        self._page = page

    def open(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=120_000)  # type: ignore[attr-defined]

    def _frames(self) -> list[object]:
        return list(self._page.frames)  # type: ignore[attr-defined]

    def text(self) -> str:
        out = []
        for frame in self._frames():
            try:
                out.append(frame.inner_text("body", timeout=2_000))  # type: ignore[attr-defined]
            except Exception:  # a frame that is loading or gone has no text yet
                continue
        return "\n".join(out)

    def count(self, selector: str) -> int:
        return sum(frame.locator(selector).count() for frame in self._frames())  # type: ignore[attr-defined]

    def click_text(self, text: str) -> bool:
        for frame in self._frames():
            button = frame.get_by_role("button", name=re.compile(text, re.I))  # type: ignore[attr-defined]
            if button.count():
                button.first.click()
                return True
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", required=True, help="the dashboard's address")
    parser.add_argument("--scored", default=SCORED_ID, help="a scored trial's registry id")
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        probe = PlaywrightProbe(page)
        for check in pages(args.scored):
            started = time.monotonic()
            failures = check_page(probe, args.url, check)
            seconds = time.monotonic() - started
            if failures:
                failed += 1
                print(f"FAIL  {check.name} ({seconds:.0f} s): {'; '.join(failures)}")
            else:
                print(f"ok    {check.name} ({seconds:.0f} s)")
        browser.close()
    print(f"{len(pages()) - failed} of {len(pages())} pages passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
