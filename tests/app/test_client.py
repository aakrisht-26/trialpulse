"""The dashboard's API client on its own (ADR 0054): what it retries, how long it waits and
what it says while it waits."""

import datetime as dt

import httpx
import pytest
import respx
from dashboard import client
from dashboard.client import Answer, ApiUnavailableError, Client

URL = "http://api.test"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(client, "sleep", c.sleep)
    monkeypatch.setattr(client, "monotonic", lambda: c.now)
    return c


ENVELOPE = {
    "notice": "Research demo. Not medical advice. Not for patient decision-making.",
    "detail": "the database is waking up; try again shortly",
    "data_as_of": None,
}


def run(clock: Clock, *responses: httpx.Response | Exception) -> tuple[Answer, list[str]]:
    messages: list[str] = []
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{URL}/v1/model").mock(side_effect=list(responses))
        answer = Client(URL).get("/v1/model", on_wait=messages.append)
    return answer, messages


def test_retry_after_reads_seconds_and_dates_within_bounds() -> None:
    now = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    assert client.retry_after("30") == 30
    assert client.retry_after("0") == 1
    assert client.retry_after("3600") == 60
    assert client.retry_after("Sun, 01 Nov 2026 12:00:20 GMT", now) == 20
    assert client.retry_after("soon") is None
    assert client.retry_after(None) is None


def test_an_answer_that_is_not_a_wake_is_returned_as_it_came(clock: Clock) -> None:
    for status in (200, 404, 422, 500):
        body = {**ENVELOPE, "data_as_of": "2026-09-25"}
        answer, messages = run(clock, httpx.Response(status, json=body))
        assert answer.status == status
        assert answer.data_as_of == dt.date(2026, 9, 25)
        assert messages == []
    assert clock.slept == []


def test_the_service_starting_is_retried_with_a_growing_wait(clock: Clock) -> None:
    answer, messages = run(
        clock, httpx.ConnectError("refused"), httpx.ReadTimeout("slow"),
        httpx.Response(502, text="Bad gateway"), httpx.Response(504),
        httpx.Response(503, text="<html>starting</html>"), httpx.Response(200, json=ENVELOPE),
    )  # fmt: skip
    assert answer.ok
    assert clock.slept == [5, 10, 20, 30, 30]
    assert messages == [client.STARTING.format(n=n) for n in (5, 10, 20, 30, 30)]


def test_the_apis_own_503_is_the_database_waking_for_its_retry_after(clock: Clock) -> None:
    answer, messages = run(
        clock, httpx.Response(503, json=ENVELOPE, headers={"Retry-After": "30"}),
        httpx.Response(200, json=ENVELOPE),
    )  # fmt: skip
    assert answer.ok
    assert clock.slept == [30]
    assert messages == ["The database is waking up; retrying in 30 seconds."]


def test_a_429_waits_for_its_retry_after(clock: Clock) -> None:
    answer, messages = run(
        clock, httpx.Response(429, json=ENVELOPE, headers={"Retry-After": "7"}),
        httpx.Response(200, json=ENVELOPE),
    )  # fmt: skip
    assert answer.ok
    assert clock.slept == [7]
    assert messages == [client.BUSY.format(n=7)]


def test_the_budget_ends_the_waiting_with_a_plain_message(clock: Clock) -> None:
    with pytest.raises(ApiUnavailableError, match="within about 3 minutes") as caught:
        run(clock, *[httpx.ConnectError("refused")] * 50)
    assert sum(clock.slept) <= client.BUDGET_SECONDS
    assert sum(clock.slept) + 30 > client.BUDGET_SECONDS  # it used the budget
    assert URL not in str(caught.value)


def test_a_slow_attempt_counts_toward_the_budget(clock: Clock) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        clock.now += client.ATTEMPT_TIMEOUT_SECONDS
        raise httpx.ReadTimeout("slow", request=request)

    with respx.mock() as router:
        router.get(f"{URL}/health").mock(side_effect=slow)
        with pytest.raises(ApiUnavailableError):
            Client(URL).get("/health")
    assert clock.now <= client.BUDGET_SECONDS + client.ATTEMPT_TIMEOUT_SECONDS


def test_each_attempt_has_a_30_second_timeout() -> None:
    c = Client(URL)
    assert c._http.timeout.read == client.ATTEMPT_TIMEOUT_SECONDS
    assert c._http.timeout.connect == client.ATTEMPT_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "page",
    [httpx.Response(200, text="<html>This Space is starting</html>"),
     httpx.Response(200, json={"status": "ok"}),  # JSON, but not the API's
     httpx.Response(200, json=["a", "list"]),
     httpx.Response(404, text="<html>Space not found</html>")],
)  # fmt: skip
def test_an_answer_that_is_not_the_apis_is_never_returned(
    clock: Clock, page: httpx.Response
) -> None:
    """Review finding 9: a 200 that was not the API's JSON reached the page (a KeyError) and
    was cached for 15 minutes. It is now waited out like the service starting."""
    answer, messages = run(clock, page, httpx.Response(200, json=ENVELOPE))
    assert answer.ok
    assert answer.body == ENVELOPE
    assert messages == [client.NOT_API.format(n=5)]


def test_an_address_that_never_answers_as_the_api_gives_up_saying_so(clock: Clock) -> None:
    with pytest.raises(ApiUnavailableError, match="not the TrialPulse API") as caught:
        run(clock, *[httpx.Response(200, text="<html>another site</html>")] * 50)
    assert sum(clock.slept) <= client.BUDGET_SECONDS
    assert URL not in str(caught.value)
