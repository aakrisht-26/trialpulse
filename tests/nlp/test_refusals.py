"""Expected refusals in the Step 6 commands print one line and exit non-zero, with no
traceback: a test split already scored, a provisional model sent to test scoring, an
uncommitted prompt, a daily limit reached, and missing inputs."""

import functools
import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from trialpulse.cli import RefusedError, StoppedEarlyError, run
from trialpulse.nlp import distill, evaluate, llm_labeler, panel
from trialpulse.nlp.gold import GoldItem, text_sha256, write_sample


def _one_line(capsys: pytest.CaptureFixture[str], prefix: str) -> str:
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.count("\n") == 1
    assert err.startswith(f"{prefix}: ")
    return err


def test_run_turns_a_refusal_into_one_line_and_an_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuses(_: object) -> int:
        raise RefusedError("first line\nsecond line")

    def stops(_: object) -> int:
        raise StoppedEarlyError("daily limit")

    def breaks(_: object) -> int:
        raise KeyError("a bug")

    assert run(refuses) == 2
    assert _one_line(capsys, "refused") == "refused: first line second line\n"
    assert run(stops) == 4
    assert _one_line(capsys, "stopped") == "stopped: daily limit\n"
    with pytest.raises(KeyError):  # a bug keeps its traceback
        run(breaks)


def test_a_test_split_already_scored_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "test_llm.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evaluate, "TEST_RESULTS_DIR", tmp_path)
    assert run(evaluate.main, ["--test", "llm"]) == 2
    assert "llm was already scored on the test split" in _one_line(capsys, "refused")


def _provisional_model(path: Path) -> None:
    texts = [f"slow enrollment {n}" for n in range(4)] + [f"grant ended {n}" for n in range(4)]
    labels = ["accrual"] * 4 + ["funding"] * 4
    pipeline, _ = distill.select_and_fit(texts, labels, seed=1, folds=2)
    meta = {"trained_at": "t", "training_texts": 1375, "sample_size": 10000}
    distill.save_model(path, pipeline, meta)


def test_a_provisional_model_is_refused_for_test_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = tmp_path / "distilled.joblib"
    _provisional_model(model)
    monkeypatch.setattr(distill, "MODEL_PATH", model)
    monkeypatch.setattr(evaluate, "TEST_RESULTS_DIR", tmp_path / "results")

    assert run(distill.main, ["--predict-test"]) == 2
    assert "the model is provisional: trained on 1375 of 10000" in _one_line(capsys, "refused")
    assert run(evaluate.main, ["--test", "distilled"]) == 2
    assert "the model is provisional" in _one_line(capsys, "refused")
    assert not (tmp_path / "results").exists()  # nothing was scored


def test_an_uncommitted_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    prompt = prompts / llm_labeler.FINAL_PROMPT
    prompt.write_text("Label reasons.\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("add", "."),
        ("commit", "-q", "-m", "prompt"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    prompt.write_text("Label reasons differently.\n", encoding="utf-8")
    checker = functools.partial(llm_labeler.committed_prompt, repo=tmp_path, prompt_dir=prompts)
    monkeypatch.setattr(llm_labeler, "committed_prompt", checker)

    assert run(llm_labeler.main, ["--split", "test"]) == 2
    assert "has uncommitted changes; commit the prompt first" in _one_line(capsys, "refused")


@respx.mock
def test_a_daily_limit_stops_with_one_line_and_exit_code_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [
        GoldItem(f"NCT{n:08d}", text_sha256(f"reason {n}"), "TERMINATED", 2015, f"Reason {n}")
        for n in range(4)
    ]
    write_sample(items, tmp_path / "sample.csv")
    monkeypatch.setattr(llm_labeler, "SAMPLE_SIZE", 4)
    monkeypatch.setattr(llm_labeler, "LLM_SAMPLE_PATH", tmp_path / "sample.csv")
    monkeypatch.setattr(llm_labeler, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm_labeler, "LABELS_DIR", tmp_path / "labels")
    monkeypatch.setattr(llm_labeler, "gold_hashes", lambda *_: set())
    monkeypatch.setattr(llm_labeler, "committed_prompt", lambda *_: "0" * 40)
    monkeypatch.setattr(llm_labeler, "_heldout", lambda *_: (set(), []))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_synthetic_test_key_value")
    respx.post(f"{llm_labeler.LLMSettings().base_url}/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "Rate limit reached on tokens per day (TPD)"}}
        )
    )

    assert run(llm_labeler.main, ["--sample", "4"]) == 4
    captured = capsys.readouterr()
    assert json.loads(captured.out)["remaining"] == 4  # the summary is still printed
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert captured.err.startswith("stopped: daily limit (TPD) reached")
    assert "0 of 4 labeled and saved; run the same command again later" in captured.err
    assert "gsk_synthetic" not in captured.err + captured.out
    stem = llm_labeler.prompt_stem(llm_labeler.FINAL_PROMPT)
    assert (tmp_path / "labels" / f"sample_{stem}.csv").is_file()


def test_missing_inputs_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(distill, "MODEL_PATH", tmp_path / "none.joblib")
    assert run(distill.main, ["--dev"]) == 2
    assert "no trained model" in _one_line(capsys, "refused")

    monkeypatch.setattr(llm_labeler, "LABELS_DIR", tmp_path / "labels")
    assert run(evaluate.main, ["--dev", "reason_v9.md"]) == 2
    assert "no dev labels for reason_v9.md" in _one_line(capsys, "refused")

    paths = ["--panel-dir", str(tmp_path / "panel"), "--sample", str(tmp_path / "s.csv")]
    assert run(panel.main, ["--finalize", *paths]) == 2
    assert "missing panel input" in _one_line(capsys, "refused")
