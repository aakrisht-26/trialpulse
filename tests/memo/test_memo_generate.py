"""The memo run (ADRs 0084 and 0085) on a seeded scratch database: every paragraph from the
template, the payload-hash skip, the check as a test of the template, the cap, deletions and
the step output. No model and no network: memo_v4 calls nothing outside the database."""

import datetime as dt
import importlib.util
from pathlib import Path
from typing import Any

import psycopg
import pytest

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.memo import check as checks
from trialpulse.memo import generate as g
from trialpulse.memo import template

spec = importlib.util.spec_from_file_location("api_seed", REPO_ROOT / "tests/api/seed.py")
assert spec is not None
assert spec.loader is not None
seeding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seeding)
DAY = seeding.DAY


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def cfg(**over: Any) -> Any:
    return load_project_config().memo.model_copy(update={"top_n": 3, "cap_per_run": 10, **over})


# Five distinct served features per horizon, as the champion's drivers always are (the shared
# seed draws random indexes, text features and repeats among them, for the API's tests):
# enrollment_count, status, sponsor_class, open_interventional_trials, planned_duration_months.
SERVED_DRIVERS = [8, 28, 44, 51, 9] * 2


@pytest.fixture
def seeded(scratch: str) -> str:
    seeding.seed(_plain(scratch))
    with psycopg.connect(_plain(scratch)) as con:
        con.execute("UPDATE serving.trial_scores_latest SET driver_features = %s",
                    (SERVED_DRIVERS,))  # fmt: skip
    return _plain(scratch)


def run(dsn: str, c: Any) -> tuple[int, g.Tally]:
    return g.run_memos(dsn, c, DAY, "test")


def memos(dsn: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as con:
        return con.execute(
            """SELECT nct_id, valid_as_of, chosen_as, cardinality(paragraphs),
               payload ? 'nct_id' FROM serving.trial_memos ORDER BY nct_id"""
        ).fetchall()


def broken_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template bug: the driver paragraph invents a number, an outcome and a comparison."""
    real = template.paragraph_two

    def bad(payload: Any) -> str:
        text = real(payload).replace(", which", " by 7777, which", 1)
        return text[:-1] + "; the trial will fail, doubled over twelve months."

    monkeypatch.setattr(template, "paragraph_two", bad)


def test_a_run_stores_template_memos_and_a_second_run_builds_nothing(seeded: str) -> None:
    run_id, tally = run(seeded, cfg())
    assert (tally.status, tally.n_chosen, tally.n_called, tally.n_stored) == ("succeeded", 3, 3, 3)
    assert (tally.n_rejected, tally.total_tokens) == (0, 0)
    stored = memos(seeded)
    assert [(m[1], m[2], m[3], m[4]) for m in stored] == [(DAY, ["top_100"], 3, True)] * 3
    with psycopg.connect(seeded) as con:
        row = con.execute(
            """SELECT status, n_called, n_stored, total_tokens, trigger, model, prompt_version
               FROM monitoring.memo_runs WHERE run_id = %s""",
            (run_id,),
        ).fetchone()
        written = con.execute(
            "SELECT DISTINCT model, prompt_version FROM serving.trial_memos"
        ).fetchall()
        rows = con.execute("SELECT paragraphs, payload FROM serving.trial_memos").fetchall()
    assert row == ("succeeded", 3, 3, 0, "test", "template", "memo_v4")
    assert written == [("template", "memo_v4")]
    for paragraphs, payload in rows:  # every paragraph is the template's, exactly
        assert list(paragraphs) == template.memo(payload)

    _, again = run(seeded, cfg())  # the same day: every payload hash unchanged
    assert (again.n_called, again.n_current, again.n_stored) == (0, 3, 0)


def test_a_template_bug_is_never_stored_and_counted_by_reason(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check stays as the test of the template at every generation (ADR 0085)."""
    broken_template(monkeypatch)
    run_id, tally = run(seeded, cfg(rejected_samples=2))
    assert (tally.n_called, tally.n_stored, tally.n_rejected) == (3, 0, 3)
    assert memos(seeded) == []
    for reason in ("number", "driver_number", "banned_language", "comparison",
                   "number_word", "driver_words"):  # fmt: skip
        assert tally.rejections[reason] == 3, reason
    assert tally.alert(0.2)
    with psycopg.connect(seeded) as con:
        samples, rejections = con.execute(
            "SELECT rejected_samples, rejections FROM monitoring.memo_runs WHERE run_id = %s",
            (run_id,)).fetchone()  # fmt: skip
    assert len(samples) == 2  # at most rejected_samples, for the review
    assert set(samples[0]) == {"nct_id", "paragraphs", "reasons", "details", "payload"}
    assert rejections["number"] == 3


def test_the_cap_limits_the_memos_of_a_run(seeded: str) -> None:
    _, tally = run(seeded, cfg(cap_per_run=2))
    assert (tally.n_called, tally.n_stored, tally.n_over_cap) == (2, 2, 1)


def test_the_configured_cap_covers_every_chosen_trial() -> None:
    """ADR 0085: with no token limit, one run covers the top 100 and the movers."""
    memo = load_project_config().memo
    assert memo.cap_per_run >= 10 * memo.top_n


def test_a_changed_payload_regenerates_and_a_trial_no_longer_chosen_is_deleted(
    seeded: str,
) -> None:
    run(seeded, cfg())
    before = {m[0] for m in memos(seeded)}
    with psycopg.connect(seeded) as con:
        top, other = con.execute(
            """SELECT nct_id FROM serving.trial_scores_latest WHERE NOT flagged
               ORDER BY risk_12m DESC, nct_id LIMIT 2""").fetchall()  # fmt: skip
        con.execute("UPDATE serving.trial_scores_latest SET risk_12m = 0.001 WHERE nct_id = %s",
                    top)  # it leaves the top 3  # fmt: skip
        con.execute("""UPDATE serving.trial_scores_latest SET risk_24m = risk_24m + 0.05
                       WHERE nct_id = %s""", other)  # its payload changes  # fmt: skip
        con.commit()
    _, tally = run(seeded, cfg())
    after = {m[0] for m in memos(seeded)}
    assert top[0] in before
    assert top[0] not in after
    assert tally.n_deleted == 1
    assert tally.n_called == 2  # the changed one and the newcomer to the top 3
    assert tally.n_current == 1


def test_a_failure_ends_the_run_as_failed(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(payload: Any) -> list[str]:
        raise RuntimeError("a template that raises")

    monkeypatch.setattr(template, "memo", broken)
    with pytest.raises(RuntimeError):
        run(seeded, cfg())
    with psycopg.connect(seeded) as con:
        row = con.execute("SELECT status, stopped_reason FROM monitoring.memo_runs").fetchone()
    assert row == ("failed", "RuntimeError")


def test_the_command_writes_the_rejection_alert_for_the_workflow(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_template(monkeypatch)  # all refused
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    small = load_project_config().model_copy(update={"memo": cfg()})
    monkeypatch.setattr(g, "load_project_config", lambda: small)
    import trialpulse.memo.generate as module

    real_now = dt.datetime

    class Frozen(real_now):  # the seeded scores are dated DAY
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_now.combine(DAY, dt.time(12), tzinfo=tz)

    monkeypatch.setattr(module.dt, "datetime", Frozen)
    assert g.main(["--target", "local", "--trigger", "workflow_dispatch"]) == 0
    assert output.read_text(encoding="utf-8").strip() == "memo_alert=rejections"


def test_the_command_needs_no_provider_key(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0085: the memo path reads no GROQ_API_KEY and writes no alert for a clean run."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    small = load_project_config().model_copy(update={"memo": cfg()})
    monkeypatch.setattr(g, "load_project_config", lambda: small)
    import trialpulse.memo.generate as module

    real_now = dt.datetime

    class Frozen(real_now):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_now.combine(DAY, dt.time(12), tzinfo=tz)

    monkeypatch.setattr(module.dt, "datetime", Frozen)
    assert g.main(["--target", "local"]) == 0
    assert output.read_text(encoding="utf-8").strip() == "memo_alert="
    assert len(memos(seeded)) == 3
    source = Path(g.__file__).read_text(encoding="utf-8")
    for word in ("groq", "llm_labeler", "httpx", "GROQ_API_KEY"):
        assert word.lower() not in source.lower(), word


def test_the_review_file_holds_the_stored_memos_and_the_rejected_ones(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trialpulse.memo import review

    with monkeypatch.context() as patch:
        broken_template(patch)
        run(seeded, cfg())  # every memo refused
    run(seeded, cfg())  # then stored
    assert review.main(["--target", "local", "--out-dir", str(tmp_path)]) == 0
    (path,) = tmp_path.glob("*.md")
    text = path.read_text(encoding="utf-8")
    assert text.count("Check run again: passed.") == 3
    assert text.count("### R") == 3
    assert "banned_language" in text
    assert text.count("Reasons: ") == 3
    assert "```json" in text
    assert "by template (memo_v4)" in text
    for name in ("Acme Pharma", "Northern University Hospital", "Hand-placed trial"):
        assert name not in text


def test_the_stored_memo_is_the_normalized_text_that_was_checked(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memo_v2: a non-breaking hyphen and a narrow space are stored as plain characters."""
    real = template.paragraph_two

    def special(payload: Any) -> str:
        text = (
            real(payload)
            .replace(" which ", "\u202fwhich ", 1)
            .replace("the risk", "the\u00a0risk", 1)
        )
        return text.replace("-", "\u2011")

    monkeypatch.setattr(template, "paragraph_two", special)
    _, tally = run(seeded, cfg())
    assert tally.n_stored == 3
    with psycopg.connect(seeded) as con:
        rows = con.execute("SELECT paragraphs, payload FROM serving.trial_memos").fetchall()
        names = [r[0] for r in con.execute(g.NAMES_SQL)]
    for paragraphs, payload in rows:
        text = "".join(paragraphs)
        assert not any(ch in text for ch in "\u2011\u202f\u00a0")
        assert checks.check(paragraphs, payload, names).paragraphs == list(paragraphs)


def test_a_memo_of_another_version_is_deleted_and_written_again(seeded: str) -> None:
    run(seeded, cfg())
    with psycopg.connect(seeded) as con:
        con.execute("UPDATE serving.trial_memos SET prompt_version = 'memo_v3', "
                    "model = 'openai/gpt-oss-120b'")  # fmt: skip
        con.commit()
    _, tally = run(seeded, cfg())
    assert (tally.n_deleted, tally.n_called, tally.n_stored) == (3, 3, 3)
    with psycopg.connect(seeded) as con:
        written = con.execute(
            "SELECT DISTINCT prompt_version, model FROM serving.trial_memos"
        ).fetchall()
    assert written == [("memo_v4", "template")]


def test_a_dry_run_reports_the_check_and_writes_nothing(seeded: str) -> None:
    found = g.dry_run(seeded, cfg(), DAY, 2)
    assert [(f["passed"], f["reasons"]) for f in found] == [(True, [])] * 2
    assert memos(seeded) == []
    with psycopg.connect(seeded) as con:
        assert con.execute("SELECT count(*) FROM monitoring.memo_runs").fetchone() == (0,)


def test_versions_are_kept_from_the_run_date_less_the_retention() -> None:
    """ADR 0069: retention deletes only versions posted before the run's date less
    `live.version_retention_months` (6)."""
    assert g.versions_kept_from(dt.date(2026, 11, 18)) == dt.date(2026, 5, 18)
    assert g.versions_kept_from(dt.date(2027, 8, 31)) == dt.date(2027, 2, 28)
