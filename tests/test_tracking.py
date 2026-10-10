"""Experiment tracking (CLAUDE.md Step 9): where runs go, what is logged, and that the
credentials of the server never show."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trialpulse import tracking
from trialpulse.config import Secrets, load_project_config


def _results() -> dict[str, Any]:
    def pooled(auc: float) -> dict[str, Any]:
        return {
            "auc": {"estimate": auc, "ci_low": auc - 0.01, "ci_high": auc + 0.01},
            "brier": {"estimate": 0.03},
            "lift": {"estimate": 1.5},
            "calibration_slope": 1.1,
            "calibration_intercept": -0.2,
        }

    return {
        "model": "m0",
        "origins_spec": "dev",
        "origins": [
            {"origin": "2016-01-01", "n_eval_rows": 100, "n_train_rows": 400,
             "horizons": {"12": {"pooled": pooled(0.54)}, "24": {"pooled": pooled(0.53)}}},
            {"origin": "2017-01-01", "n_eval_rows": 120, "n_train_rows": 500,
             "horizons": {"12": {"pooled": pooled(0.56)}, "24": {"pooled": pooled(0.55)}}},
        ],
    }  # fmt: skip


def test_walk_forward_metrics_are_one_number_per_origin_and_horizon_and_their_mean() -> None:
    metrics = tracking.walkforward_metrics(_results())
    assert metrics["auc_12m_2016"] == 0.54
    assert metrics["auc_ci_low_12m_2016"] == pytest.approx(0.53)
    assert metrics["auc_24m_2017"] == 0.55
    # The selection rule of Section 9 reads the mean AUC at 12 months over the origins.
    assert metrics["auc_12m_mean"] == pytest.approx(0.55)
    assert metrics["brier_12m_mean"] == pytest.approx(0.03)
    assert metrics["calibration_slope_24m_mean"] == pytest.approx(1.1)
    assert "auc_ci_low_12m_mean" not in metrics  # an interval is not averaged
    assert metrics["n_eval_rows_2017"] == 120.0
    assert all(isinstance(v, float) for v in metrics.values())


def test_without_a_server_runs_go_to_a_local_store(tmp_path: Path) -> None:
    secrets = Secrets(_env_file=None, mlflow_tracking_uri=None)  # type: ignore[call-arg]
    target = tracking.tracking_target(secrets, tmp_path / "mlruns")
    assert target.kind == tracking.LOCAL
    assert target.uri == "sqlite:///" + (tmp_path / "mlruns" / "mlflow.db").resolve().as_posix()
    assert target.artifacts == (tmp_path / "mlruns" / "artifacts").resolve().as_uri()
    assert target.description == (
        "a local store in mlruns/ (temporary: MLFLOW_TRACKING_URI is not set)"
    )
    assert (tmp_path / "mlruns").is_dir()


def test_the_local_store_can_be_asked_for_although_a_server_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For the time a configured server cannot take runs. The description says why the
    run is local, so a results file never passes a stand-in off as the record."""
    monkeypatch.delenv("MLFLOW_TRACKING_PASSWORD", raising=False)
    secrets = Secrets(
        _env_file=None,  # type: ignore[call-arg]
        mlflow_tracking_uri="https://tracking.example.test/owner/repo.mlflow",
        mlflow_tracking_password="not-a-real-password",  # type: ignore[arg-type]
    )
    target = tracking.tracking_target(secrets, tmp_path / "mlruns", local=True)
    assert target.kind == tracking.LOCAL
    assert target.uri.startswith("sqlite:///")
    assert "temporary: --local-tracking was given" in target.description
    assert "MLFLOW_TRACKING_PASSWORD" not in os.environ  # the server is not prepared at all


def test_a_failure_is_explained_on_one_line_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_PASSWORD", "not-a-real-password")
    server = tracking.Target(tracking.REMOTE, "https://tracking.example.test/owner/repo.mlflow")
    refused = RuntimeError(
        "API request to endpoint /api/2.0/mlflow/experiments/get-by-name failed with error "
        "code 404 != 200.\nResponse body: ''"
    )
    text = tracking.explain(refused, server)
    assert "\n" not in text
    assert text.startswith("API request to endpoint")
    assert "the repository named in MLFLOW_TRACKING_URI does not exist there" in text
    leaked = RuntimeError("the server refused the password not-a-real-password")
    assert tracking.explain(leaked, server) == "the server refused the password [password]"
    # Credentials written inside an address go too, whatever they are.
    address = RuntimeError("could not reach https://someone:another-token@host/x?y=1")
    assert tracking.explain(address, server) == "could not reach https://[credentials]@host/x?y=1"
    assert tracking.explain(RuntimeError("see https://host/a@b"), server) == "see https://host/a@b"
    # A token alone, a password that holds an "@", and every address of a message.
    masked = "https://[credentials]@host/x"
    assert tracking.explain(RuntimeError("https://only-a-token@host/x"), server) == masked
    assert tracking.explain(RuntimeError("https://user:p@ss@host/x"), server) == masked
    both = tracking.explain(RuntimeError("see https://a:b@h/x and https://c@i/y"), server)
    assert both == "see https://[credentials]@h/x and https://[credentials]@i/y"
    # An "@" after the host begins no user information, and neither does the user of an
    # address in the short form git uses.
    for plain in ("https://host?mail=a@b.test", "https://host#part@x", "git@host.test:o/r.git"):
        assert tracking.explain(RuntimeError(plain), server) == plain
    # Before a target exists (the store could not even be chosen) there is no hint to add.
    assert tracking.explain(RuntimeError("error 404"), None) == "error 404"
    # The hint is about a server; a local store that fails gets its own message only.
    local = tracking.Target(tracking.LOCAL, "sqlite:///x.db")
    assert tracking.explain(RuntimeError("error 404"), local) == "error 404"
    assert tracking.explain(RuntimeError(""), local) == "RuntimeError"


def test_a_configured_server_is_used_and_its_password_is_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set, then removed: monkeypatch then knows both names and takes away at the end of the
    # test what tracking_target writes into the environment of this process.
    for name in ("MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    secrets = Secrets(
        _env_file=None,  # type: ignore[call-arg]
        mlflow_tracking_uri="https://tracking.example.test/owner/repo.mlflow",
        mlflow_tracking_username="someone",
        mlflow_tracking_password="not-a-real-password",  # type: ignore[arg-type]
    )
    target = tracking.tracking_target(secrets, tmp_path / "mlruns")
    assert target.kind == tracking.REMOTE
    assert target.uri == "https://tracking.example.test/owner/repo.mlflow"
    assert not (tmp_path / "mlruns").exists()  # no local store beside a server
    # MLflow reads the credentials from the environment of this process.
    assert os.environ["MLFLOW_TRACKING_USERNAME"] == "someone"
    assert os.environ["MLFLOW_TRACKING_PASSWORD"] == "not-a-real-password"
    for shown in (repr(target), str(target), target.description, repr(secrets)):
        assert "not-a-real-password" not in shown
    # An address can hold credentials of its own, so a target never shows its address.
    inside = tracking.Target(tracking.REMOTE, "https://someone:a-token-in-the-address@host/x")
    assert "a-token-in-the-address" not in repr(inside)
    assert "host/x" not in repr(inside)
    assert inside.uri.endswith("@host/x")  # MLflow still gets it


def test_the_environment_is_left_as_it_was_by_the_test_above() -> None:
    """The fake credentials of the test above must not reach the tests that run after it."""
    assert os.environ.get("MLFLOW_TRACKING_PASSWORD") != "not-a-real-password"
    assert os.environ.get("MLFLOW_TRACKING_USERNAME") != "someone"


def test_the_git_state_is_the_commit_and_whether_tracked_files_changed(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "first")
    assert tracking.git_state(tmp_path) == {
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": "no",
    }
    (tmp_path / "untracked.txt").write_text("x\n", encoding="utf-8")
    assert tracking.git_state(tmp_path)["git_dirty"] == "no"  # an untracked file is not a change
    (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")
    assert tracking.git_state(tmp_path)["git_dirty"] == "yes"
    assert tracking.git_state(tmp_path / "missing")["git_commit"] == "unknown"


@pytest.mark.slow
def test_a_run_is_logged_with_its_commit_dataset_config_metrics_and_files(
    tmp_path: Path, real_tracking: None
) -> None:
    """The real MLflow client against a local store of this test's own."""
    import mlflow

    cfg = load_project_config()
    target = tracking.local_target(tmp_path / "store")
    artifact = tmp_path / "result.json"
    artifact.write_text(json.dumps({"auc": 0.5}), encoding="utf-8")
    info = tracking.log_run(
        "m0-dev",
        {"model": "m0", "bootstrap_resamples": 20},
        {"auc_12m_mean": 0.55, "not_a_number": float("nan")},
        [artifact, tmp_path / "missing.json"],
        cfg,
        tags={"kind": "walk-forward"},
        target=target,
    )
    assert info["store"] == tracking.LOCAL
    assert info["experiment"] == tracking.EXPERIMENT
    mlflow.set_tracking_uri(target.uri)
    run = mlflow.get_run(info["run_id"])
    assert run.data.params == {"model": "m0", "bootstrap_resamples": "20"}
    assert run.data.metrics == {"auc_12m_mean": 0.55}  # the value that is not finite is left out
    tags = run.data.tags
    assert tags["metrics_not_finite"] == "1"
    assert tags["dataset_revision"] == str(cfg.dataset.revision)
    assert tags["data_cutoff"] == str(cfg.dataset.cutoff)
    assert tags["kind"] == "walk-forward"
    assert tags["git_commit"] == info["git_commit"] != ""
    assert tags["git_dirty"] in ("yes", "no")
    stored = {f.path for f in mlflow.artifacts.list_artifacts(run_id=info["run_id"])}
    assert stored == {"result.json", "project.yaml"}
    assert (tmp_path / "store" / "mlflow.db").is_file()
    # A second run joins the same experiment.
    again = tracking.log_run("m0-dev", {}, {"auc_12m_mean": 0.56}, [], cfg, target=target)
    assert again["run_id"] != info["run_id"]
    assert mlflow.get_run(again["run_id"]).info.experiment_id == run.info.experiment_id


@pytest.mark.slow
def test_a_store_that_refuses_a_run_raises_a_tracking_error_with_the_state_it_was_given(
    tmp_path: Path, real_tracking: None
) -> None:
    """The real client: a run is credited to the state handed in, and a store that cannot
    be opened gives a TrackingError, not whatever the client raises."""
    import mlflow

    cfg = load_project_config()
    target = tracking.local_target(tmp_path / "store")
    started = {"git_commit": "0123abcd", "git_dirty": "yes"}
    info = tracking.log_run("m1-dev", {}, {"auc": 0.6}, [], cfg, target=target, state=started)
    assert (info["git_commit"], info["git_dirty"]) == ("0123abcd", "yes")
    mlflow.set_tracking_uri(target.uri)
    assert mlflow.get_run(info["run_id"]).data.tags["git_commit"] == "0123abcd"
    broken = tracking.Target(tracking.LOCAL, "no-such-scheme://nowhere")
    with pytest.raises(tracking.TrackingError):
        tracking.log_run("m1-dev", {}, {"auc": 0.6}, [], cfg, target=broken)


def test_only_finite_metrics_are_logged() -> None:
    kept = tracking.finite_metrics(
        {"auc": 0.6, "slope": float("nan"), "up": float("inf"), "down": float("-inf"), "n": 3}
    )
    assert kept == {"auc": 0.6, "n": 3.0}
    assert all(isinstance(value, float) for value in kept.values())


def test_the_source_of_a_run_is_a_path_inside_the_repository(tmp_path: Path) -> None:
    """MLflow would log the path of the script on the disk of the machine. A run names the
    command by its path inside the repository, and by its file name when it is elsewhere."""
    script = tmp_path / "src" / "trialpulse" / "eval" / "walkforward.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    assert tracking.source_name(tmp_path, str(script)) == "src/trialpulse/eval/walkforward.py"
    assert tracking.source_name(tmp_path / "elsewhere", str(script)) == "walkforward.py"
    assert tracking.source_name(tmp_path, "") == "unknown"
    assert not Path(tracking.source_name()).is_absolute()


def test_the_address_of_the_git_remote_is_logged_without_credentials(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    assert tracking.remote_address(tmp_path / "missing") == ""
    git("init", "-q")
    assert tracking.remote_address(tmp_path) == ""  # no remote
    git("remote", "add", "origin", "https://someone:a-token@example.test/owner/repo.git")
    assert tracking.remote_address(tmp_path) == "https://example.test/owner/repo.git"
    git("remote", "set-url", "origin", "https://example.test/owner/repo.git")
    assert tracking.remote_address(tmp_path) == "https://example.test/owner/repo.git"
    git("remote", "set-url", "origin", "https://only-a-token@example.test/owner/repo.git")
    assert tracking.remote_address(tmp_path) == "https://example.test/owner/repo.git"
    git("remote", "set-url", "origin", "git@example.test:owner/repo.git")  # no credentials
    assert tracking.remote_address(tmp_path) == "git@example.test:owner/repo.git"


@pytest.mark.slow
def test_a_run_carries_the_project_s_values_in_the_tags_mlflow_sets_by_itself(
    tmp_path: Path, real_tracking: None
) -> None:
    """MLflow fills these four tags from the machine: HEAD when the run is logged, the path
    of the script on the disk, the login name, the remote as git has it. A run must carry
    the commit it started from, and nothing that describes the machine."""
    import mlflow

    cfg = load_project_config()
    target = tracking.local_target(tmp_path / "store")
    started = {"git_commit": "0123abcd", "git_dirty": "no"}
    info = tracking.log_run("m0-dev", {}, {"auc": 0.6}, [], cfg, target=target, state=started)
    mlflow.set_tracking_uri(target.uri)
    tags = mlflow.get_run(info["run_id"]).data.tags
    assert tags["mlflow.source.git.commit"] == "0123abcd" == tags["git_commit"]
    assert tags["mlflow.user"] == tracking.RUN_USER == "trialpulse"
    assert tags["mlflow.source.name"] == tracking.source_name()
    assert not Path(tags["mlflow.source.name"]).is_absolute()
    assert tags["mlflow.source.git.repoURL"] == tracking.remote_address()
    assert tracking.CREDENTIALS_IN_ADDRESS.search(tags["mlflow.source.git.repoURL"]) is None


@pytest.mark.slow
def test_the_local_option_and_every_failure_before_the_store_reach_log_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_tracking: None
) -> None:
    """log_run chooses its store with the `local` it was given, and a failure while choosing
    it is a TrackingError like any other: the command that called keeps its results."""
    cfg = load_project_config()
    asked: list[bool] = []

    def choose(secrets: object = None, local_store: object = None, local: bool = False) -> Any:
        asked.append(local)
        return tracking.local_target(tmp_path / ("local" if local else "server"))

    monkeypatch.setattr(tracking, "tracking_target", choose)
    info = tracking.log_run("m1-dev", {}, {"auc": 0.6}, [], cfg, local=True)
    assert asked == [True]
    assert (tmp_path / "local" / "mlflow.db").is_file()
    assert not (tmp_path / "server").exists()
    assert info["store"] == tracking.LOCAL
    tracking.log_run("m1-dev", {}, {"auc": 0.6}, [], cfg)
    assert asked == [True, False]

    def no_store(*args: object, **kwargs: object) -> Any:
        raise PermissionError("the folder of the local store cannot be created")

    monkeypatch.setattr(tracking, "tracking_target", no_store)
    with pytest.raises(tracking.TrackingError, match="cannot be created"):
        tracking.log_run("m1-dev", {}, {"auc": 0.6}, [], cfg)
