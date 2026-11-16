"""A short demo of the dashboard as docs/figures/demo.gif (Step 18).

    uv run python scripts/demo_gif.py --url http://localhost:8501

The dashboard must read the live API (`API_URL` in .streamlit/secrets.toml, gitignored). With
Playwright (the installed Chrome, headless) it walks through Overview, Portfolio (a large
organization's open trials), Trial detail (a scored trial: its score card, drivers and
timeline) and Model health, waits for each page to finish (scripts/check_dashboard.py), and
fails on a page whose text holds a personal title (Dr, Prof, MD, PhD), so no frame with a
person's name is saved. The frames share one 64-colour palette and the file must stay under
the repository's 500 KB limit; the script fails rather than write a larger one.
"""

import argparse
import importlib.util
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "scripts" / "dashboard_screenshots.py"
spec = importlib.util.spec_from_file_location("shots", SHOTS)
assert spec is not None
assert spec.loader is not None
shots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shots)
check = shots.check

OUT = shots.FIGURES / "demo.gif"
LIMIT = 500 * 1024
WIDTH = 960  # each frame's width in the file
# (page, path, pixels scrolled for each frame after the first, seconds per frame)
WALK = (
    ("Overview", "/", (0,), 2500),
    ("Portfolio", "/portfolio", (0, 500), 2500),
    ("Trial detail", f"/trial?nct={shots.SCORED}", (0, 650, 1300), 2500),
    ("Model health", "/model-health", (0, 2600, 3900), 2500),
)


def frame(png: bytes):  # type: ignore[no-untyped-def]
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    height = round(image.height * WIDTH / image.width)
    return image.resize((WIDTH, height), Image.Resampling.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    from PIL import Image
    from playwright.sync_api import sync_playwright

    frames, durations = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 760}, device_scale_factor=1)
        probe = check.PlaywrightProbe(page)
        probe.open(args.url.rstrip("/") + "/")  # a first visit lets Streamlit list its pages
        check.wait_finished(probe, check.BUDGET_SECONDS, time.monotonic, time.sleep)
        for name, path, scrolls, ms in WALK:
            probe.open(args.url.rstrip("/") + path)
            if not check.wait_finished(probe, check.BUDGET_SECONDS, time.monotonic, time.sleep):
                print(f"FAIL  {name}: the page did not finish")
                return 1
            if name == "Portfolio":
                page.get_by_label("Sponsor name starts with").fill(shots.SPONSOR)
                page.keyboard.press("Enter")
                page.get_by_text("open trials, ranked by early-stop risk").wait_for(timeout=240_000)
            time.sleep(4)  # charts draw after the script's run ends
            text = page.inner_text('[data-testid="stMain"]')
            if shots.PERSONAL.search(text):
                print(f"FAIL  {name}: a personal title on the page")
                return 1
            if check.NOTICE not in text:
                print(f"FAIL  {name}: no notice")
                return 1
            main_area = page.locator('[data-testid="stMain"]')
            for offset in scrolls:
                main_area.evaluate("(el, y) => el.scrollTo(0, y)", offset)
                time.sleep(1.5)
                frames.append(frame(page.screenshot()))
                durations.append(ms)
            print(f"ok    {name}: {len(scrolls)} frames")
        browser.close()
    thumbs = [f.resize((f.width // 4, f.height // 4)) for f in frames]  # every page's colours
    mosaic = Image.new("RGB", (thumbs[0].width, sum(t.height for t in thumbs)))
    top = 0
    for t in thumbs:
        mosaic.paste(t, (0, top))
        top += t.height
    palette = mosaic.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=durations, loop=0,
                   optimize=True, disposal=1)  # fmt: skip
    size = OUT.stat().st_size
    print(f"{OUT.name}: {len(images)} frames, {size / 1024:.0f} KB")
    if size >= LIMIT:
        OUT.unlink()
        print("FAIL  over the repository's 500 KB limit: fewer frames or a smaller width")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
