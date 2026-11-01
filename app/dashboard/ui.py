"""What every page shares: the frame (the notice on top; the risk wording, the attribution
and the data-as-of date at the foot), the cached API calls with their visible waits
(ADR 0054), and the parts that show a trial or a flag (ADRs 0052 and 0055).

The frame is drawn whatever happens: when the API does not answer, the page says so in plain
words with a "Try again" button, and the notice and the attribution stay on it.
"""

import datetime as dt
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import streamlit as st

from dashboard import client as api_client
from dashboard.client import Answer, ApiUnavailableError, Client

# The API's own wording (src/trialpulse/api/schemas.py); tests/app/test_pages.py holds these
# to the recorded answers, so the dashboard shows them even when the API does not answer.
NOTICE = "Research demo. Not medical advice. Not for patient decision-making."
SOURCE_URL = "https://clinicaltrials.gov"
SOURCE_NOTE = (
    "TrialPulse normalizes the registry records and adds derived early-stop risk scores and "
    "drivers, which are not part of ClinicalTrials.gov."
)
RISK_WORDING = (
    "TrialPulse is operational risk analytics for trial portfolios. A score is an estimated "
    "probability of an early stop (terminated or withdrawn) and describes elevated early-stop "
    "risk; it says nothing about whether a treatment works."
)
FLAG_BADGE = ":orange-badge[:material/warning: Outside the validated range]"
FLAG_CAPTION = (
    "Scores marked outside the validated range belong to trials older than every row the model "
    "was tested on. In development such scores ran above the observed risk, so they may "
    "overstate it."
)
REGISTRY_STUDY = "https://clinicaltrials.gov/study/{nct_id}"
NCT = re.compile(r"^NCT\d{8}$")

CACHE_SECONDS = 15 * 60  # ADR 0054
CACHE_ENTRIES = 2000
DEFAULT_API_URL = "http://localhost:8000"

# A seam for the tests: today's date, for the age of the data.
today = dt.date.today


class UnexpectedAnswerError(Exception):
    """The API answered with a status the page cannot use."""


def api_url() -> str:
    """The API's address: `API_URL` in Streamlit's secrets, else the `TRIALPULSE_API_URL`
    environment variable for a local run, else the local default."""
    try:
        secret = st.secrets.get("API_URL")
    except Exception:  # no secrets file at all: Streamlit raises rather than returning None
        secret = None
    return str(secret or os.environ.get("TRIALPULSE_API_URL") or DEFAULT_API_URL)


class _AnswerCache:
    """Answers kept for 15 minutes in the dashboard's process, shared by every viewer (the
    answers are the same for everyone). Only answers that hold for the data date are kept:
    200 and 404; a failure is never kept."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[Any, ...], tuple[float, Answer]] = OrderedDict()

    def get(self, key: tuple[Any, ...], now: float) -> Answer | None:
        with self._lock:
            item = self._items.get(key)
            if item is None or now - item[0] > CACHE_SECONDS:
                return None
            self._items.move_to_end(key)
            return item[1]

    def put(self, key: tuple[Any, ...], now: float, answer: Answer) -> None:
        with self._lock:
            self._items[key] = (now, answer)
            self._items.move_to_end(key)
            while len(self._items) > CACHE_ENTRIES:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_cache = _AnswerCache()
_clients: dict[str, Client] = {}
_clients_lock = threading.Lock()


def reset() -> None:
    """Forget every kept answer and client (tests)."""
    _cache.clear()
    with _clients_lock:
        for c in _clients.values():
            c.close()
        _clients.clear()


def _client(url: str) -> Client:
    with _clients_lock:
        if url not in _clients:
            _clients[url] = Client(url)
        return _clients[url]


class Page:
    """One run of a page: its API calls and the newest data-as-of date they carried."""

    def __init__(self) -> None:
        self.data_as_of: dt.date | None = None
        self.url = api_url()

    def get(self, path: str, params: dict[str, Any] | None = None,
            allow: tuple[int, ...] = ()) -> Answer:  # fmt: skip
        """The API's answer to a GET, from the cache when it is fresh. Raises
        `ApiUnavailableError` after the waking budget and `UnexpectedAnswerError` for a status
        that is neither 200 nor in `allow`."""
        key = (self.url, path, tuple(sorted((params or {}).items())))
        now = api_client.monotonic()
        answer = _cache.get(key, now)
        if answer is None:
            answer = self._fetch(path, params)
            if answer.status in (200, 404):
                _cache.put(key, now, answer)
        if answer.data_as_of and (self.data_as_of is None or answer.data_as_of > self.data_as_of):
            self.data_as_of = answer.data_as_of
        if answer.status != 200 and answer.status not in allow:
            raise UnexpectedAnswerError(answer.status)
        return answer

    def _fetch(self, path: str, params: dict[str, Any] | None) -> Answer:
        status = st.empty()

        def waiting(message: str) -> None:
            status.info(message, icon=":material/hourglass_top:")

        try:
            if not st.session_state.get("api_awake"):
                # /health never touches the database: the service's start is waited for
                # here, and the database's wake on the data call below (ADR 0054).
                _client(self.url).get("/health", on_wait=waiting)
                st.session_state["api_awake"] = True
            return _client(self.url).get(path, params, on_wait=waiting)
        finally:
            status.empty()


@contextmanager
def page(title: str, intro: str | None = None) -> Iterator[Page]:
    """The frame of a page around its body."""
    st.title(title)
    st.warning(NOTICE, icon=":material/info:")
    if intro:
        st.markdown(intro)
    current = Page()
    try:
        yield current
    except ApiUnavailableError as exc:
        st.session_state["api_awake"] = False
        st.error(str(exc), icon=":material/cloud_off:")
        st.button("Try again", key="try_again")  # a click reruns the page
    except UnexpectedAnswerError as exc:
        st.error(
            f"The API answered with an error (status {exc.args[0]}). Try again later.",
            icon=":material/error:",
        )
        st.button("Try again", key="try_again")
    finally:
        footer(current.data_as_of)


def run(body: Callable[[Page], None], title: str, intro: str | None = None) -> None:
    """Draw a page: its frame around `body`. A body ends early with `return`, never by
    stopping the script (st.stop): then Streamlit drops what the frame draws after it, the
    attribution and the data-as-of date among them (AppTest still shows them, so
    tests/app/test_imports.py forbids st.stop under app/)."""
    with page(title, intro) as current:
        body(current)


def footer(data_as_of: dt.date | None) -> None:
    st.divider()
    st.caption(RISK_WORDING)
    when = (
        f"Data as of {data_as_of.isoformat()} (the date ClinicalTrials.gov processed the data)."
        if data_as_of
        else "Data as of: not known yet (the API has not answered)."
    )
    st.caption(f"Source: [ClinicalTrials.gov]({SOURCE_URL}). {SOURCE_NOTE} {when}")
    st.caption(NOTICE)


def registry_link(nct_id: str) -> str:
    return REGISTRY_STUDY.format(nct_id=nct_id)


def percent(value: float | None) -> str:
    return "" if value is None else f"{value:.1%}"


def sponsor_label(name: str | None, sponsor_class: str | None) -> str:
    """A sponsor as the API shows it: its display name, or only its class (ADR 0047)."""
    if name:
        return f"{name} ({sponsor_class or 'class unknown'})"
    return f"Name not shown (sponsor class {sponsor_class or 'unknown'})"


REASON_WORDS = {
    "reversal": "status reversal",
    "beyond_120_months": "registered 120 or more months ago",
    "band_not_validated": "age band not validated",
}


def trial_rows(trials: list[dict[str, Any]], hide_flagged: bool) -> list[dict[str, Any]]:
    """Rows of a ranking (ADR 0055): flagged trials stay in place with a flag column unless
    the viewer hides them; a trial without a score shows its reason."""
    rows = []
    for rank, t in enumerate(trials, start=1):
        if hide_flagged and t.get("flagged"):
            continue
        scored = t["status"] == "scored"
        rows.append({
            "Rank": str(rank) if scored else "",
            "Registry id": registry_link(t["nct_id"]),
            "Title": t.get("brief_title") or "",
            "Early-stop risk, 12 months": percent(t.get("risk_12m")),
            "Early-stop risk, 24 months": percent(t.get("risk_24m")),
            "Flag": "Outside the validated range" if t.get("flagged") else "",
            "Not scored": "" if scored else REASON_WORDS.get(t.get("reason") or "", "no score"),
            "Scored on": t.get("as_of_date") or "",
            "Detail": f"trial?nct={t['nct_id']}",
        })  # fmt: skip
    return rows


def show_trials(trials: list[dict[str, Any]], key: str) -> None:
    """A ranking with its flag caption and its "Hide flagged" switch (off by default)."""
    hide = st.toggle("Hide flagged", value=False, key=f"hide_flagged_{key}")
    st.caption(FLAG_CAPTION)
    rows = trial_rows(trials, hide)
    if not rows:
        st.info("No trials to show.")
        return
    st.dataframe(
        rows,
        hide_index=True,
        column_config={
            "Registry id": st.column_config.LinkColumn(
                "Registry id",
                display_text=r"https://clinicaltrials\.gov/study/(NCT\d{8})",
                help="The trial's page on ClinicalTrials.gov",
            ),
            "Detail": st.column_config.LinkColumn(
                "Detail", display_text="Open", help="The trial's page on this dashboard"
            ),
        },
    )
