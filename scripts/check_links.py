"""Check the links of README.md, CLAUDE.md and docs/ (Step 18).

    uv run python scripts/check_links.py              (offline: relative links and anchors)
    uv run python scripts/check_links.py --external   (also each external link, by hand)

Offline, every relative link must name a file or folder that exists, and every anchor
(`#part-4-backups`, or `file.md#anchor`) a heading of that Markdown file, by GitHub's rule for
heading anchors. Links inside code blocks and inline code are not links. The external mode
asks each distinct http(s) address once, politely (one request a second, HEAD then GET, a
generic client name), and is never run by the tests, which are offline.
"""

import argparse
import re
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(
    r"(?<!!)\[(?:[^\]\[]|\[[^\]]*\])*\]"  # the text, one level of brackets inside
    r"\(([^()\s]+(?:\([^()]*\))?[^()\s]*)(?:\s+\"[^\"]*\")?\)"  # the target, a title
)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^()\s]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Broken:
    file: str
    line: int
    target: str
    why: str


def documents(root: Path = ROOT) -> list[Path]:
    found = [root / "README.md", root / "CLAUDE.md", root / "CHANGELOG.md"]
    found += sorted((root / "docs").rglob("*.md"))
    return [p for p in found if p.is_file()]


def _prose_lines(text: str) -> Iterable[tuple[int, str]]:
    """The lines outside code blocks, with inline code removed."""
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, re.sub(r"`[^`]*`", "", line)


def slug(heading: str) -> str:
    """GitHub's anchor of a heading: markup removed, lower case, punctuation dropped (word
    characters, spaces and hyphens kept), spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # a link keeps its text
    text = re.sub(r"[*_`]", "", text) if "`" in text or "*" in text else text
    text = text.replace("`", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    for _, line in _prose_lines(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.add(base if count == 0 else f"{base}-{count}")
    return out


def links(path: Path) -> Iterable[tuple[int, str]]:
    raw = path.read_text(encoding="utf-8")
    fenced = False
    for number, line in enumerate(raw.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        prose = re.sub(r"`[^`]*`", "", line)
        for pattern in (LINK, IMAGE):
            for match in pattern.finditer(prose):
                yield number, match.group(1)


def check(paths: Sequence[Path], root: Path = ROOT) -> tuple[list[Broken], set[str]]:
    """The broken relative links and anchors, and every external address found."""
    broken: list[Broken] = []
    external: set[str] = set()
    cache: dict[Path, set[str]] = {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        for number, target in links(path):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                if target.startswith(("http://", "https://")):
                    external.add(target)
                continue  # mailto: and the like
            file_part, _, anchor = target.partition("#")
            resolved = (path.parent / file_part).resolve() if file_part else path
            if file_part and not resolved.exists():
                broken.append(Broken(name, number, target, "no such file"))
                continue
            if anchor and resolved.suffix == ".md":
                if resolved not in cache:
                    cache[resolved] = anchors(resolved)
                if anchor.lower() not in cache[resolved]:
                    broken.append(Broken(name, number, target, "no such heading"))
    return broken, external


def check_external(addresses: Iterable[str], pause: float = 1.0) -> list[tuple[str, str]]:
    """Each address asked once (HEAD, then GET if HEAD is refused); the ones that fail."""
    import httpx

    failed = []
    with httpx.Client(follow_redirects=True, timeout=30,
                      headers={"User-Agent": "link-check"}) as client:  # fmt: skip
        for address in sorted(addresses):
            try:
                answer = client.head(address)
                if answer.status_code >= 400:
                    answer = client.get(address)
                if answer.status_code >= 400:
                    failed.append((address, str(answer.status_code)))
            except httpx.HTTPError as exc:
                failed.append((address, type(exc).__name__))
            time.sleep(pause)
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the documents' links.")
    parser.add_argument("--external", action="store_true", help="also ask each external link")
    args = parser.parse_args(argv)
    broken, external = check(documents())
    for b in broken:
        print(f"{b.file}:{b.line}: {b.target} ({b.why})")
    print(f"{len(broken)} broken relative links or anchors in {len(documents())} documents; "
          f"{len(external)} distinct external links")  # fmt: skip
    failed = check_external(external) if args.external else []
    for address, why in failed:
        print(f"external: {address} ({why})")
    if args.external:
        print(f"{len(failed)} external links failed")
    return 1 if broken or failed else 0


if __name__ == "__main__":
    sys.exit(main())
