"""Screenshots of every dashboard page for docs/figures/ (Step 16's acceptance).

    uv run python scripts/dashboard_screenshots.py --url http://localhost:8501

The dashboard must read the live API (`API_URL` in .streamlit/secrets.toml, gitignored). With
Playwright (the installed Chrome, headless, device scale factor 2) it opens each page, waits
for it to finish (scripts/check_dashboard.py), checks that it shows the notice and the
attribution, and saves `docs/figures/dashboard_<page>.png`. A page of trials shows registry
titles and organization names only (ADR 0059); the script also counts personal titles (Dr,
Prof, MD, PhD) in each page's text and fails on any, so a capture with a person's name is
never saved.
"""

import argparse
import importlib.util
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_dashboard", ROOT / "scripts" / "check_dashboard.py"
)
assert spec is not None
assert spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

FIGURES = ROOT / "docs" / "figures"
PERSONAL = re.compile(r"\b(Dr|Prof|MD|PhD|M\.D\.|Ph\.D\.)\b")
SPONSOR = "Pfizer"  # a large organization whose name ADR 0059 shows
SCORED = "NCT06447662"  # scored, not flagged, with a timeline point (2026-11-10)
FLAGGED = "NCT05160922"  # scored and flagged "outside the validated range" (2026-11-10)
SHOTS = (
    ("overview", "/", None),
    ("portfolio", "/portfolio", "portfolio"),
    ("trial_detail", f"/trial?nct={SCORED}", None),
    ("trial_detail_flagged", f"/trial?nct={FLAGGED}", "Outside the validated range"),
    ("landscape", "/landscape", None),
    ("why_trials_stop", "/why-trials-stop", None),
    ("model_health", "/model-health", None),
)


def shrink(path: Path) -> None:
    """A 256-colour palette PNG: near-lossless for flat interface colours, and it keeps each
    figure under the repository's 500 KB file limit (pre-commit) at device scale factor 2."""
    from PIL import Image

    with Image.open(path) as image:
        small = image.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    small.save(path, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1700}, device_scale_factor=2)
        probe = check.PlaywrightProbe(page)
        for name, path, extra in SHOTS:
            probe.open(args.url.rstrip("/") + path)
            if not check.wait_finished(probe, check.BUDGET_SECONDS, time.monotonic, time.sleep):
                print(f"FAIL  {name}: the page did not finish")
                failed += 1
                continue
            if extra == "portfolio":
                page.get_by_label("Sponsor name starts with").fill(SPONSOR)
                page.keyboard.press("Enter")
                page.get_by_text("open trials, ranked by early-stop risk").wait_for(timeout=240_000)
                extra = None
            time.sleep(4)  # charts draw after the script's run ends
            text = page.inner_text('[data-testid="stMain"]')
            wanted = (
                ("the notice", check.NOTICE in text),
                ("the attribution", check.SOURCE in text),
                (str(extra), extra is None or extra in text),
            )
            problems = [what for what, ok in wanted if not ok]
            titles = PERSONAL.findall(text)
            if titles:
                problems.append(f"{len(titles)} personal titles")
            if problems:
                print(f"FAIL  {name}: missing or found {', '.join(problems)}")
                failed += 1
                continue
            # Streamlit scrolls inside its main area, so the page is as tall as the window: make
            # the window as tall as the content, so the capture holds the footer (attribution).
            tall = page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")
            page.set_viewport_size({"width": 1440, "height": int(tall) + 40})
            time.sleep(2)
            out = FIGURES / f"dashboard_{name}.png"
            page.screenshot(path=str(out), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1700})
            shrink(out)
            print(f"ok    {name}: {out.relative_to(ROOT).as_posix()}")
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
