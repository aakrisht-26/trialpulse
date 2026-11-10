"""Neon is migrated only through an explicit URL, and tests touch local databases only
(ADR 0062, review finding 10)."""

from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config

from trialpulse.cli import RefusedError
from trialpulse.config import REPO_ROOT
from trialpulse.live import migrate

from ..conftest import TEST_HOSTS, check_test_host

psycopg = pytest.importorskip("psycopg")
NEON = "postgresql+psycopg://someone:secret@ep-example-123.eu-central-1.aws.neon.tech/neondb"


@pytest.mark.parametrize("host", ["ep-example-123.eu-central-1.aws.neon.tech", "db.example.org",
                                  "10.0.0.5"])  # fmt: skip
def test_the_test_fixtures_refuse_a_remote_host(host: str) -> None:
    with pytest.raises(pytest.fail.Exception, match=f"refused: DATABASE_URL points at {host}"):
        check_test_host(f"postgresql://u:p@{host}:5432/db")


@pytest.mark.parametrize("host", TEST_HOSTS)
def test_the_test_fixtures_accept_the_local_hosts(host: str) -> None:
    check_test_host(f"postgresql://u:p@{host}:15432/db")


def test_cis_postgres_service_is_a_test_host() -> None:
    workflow = yaml.safe_load(Path(REPO_ROOT, ".github/workflows/ci.yml").read_text("utf-8"))
    urls = [step["env"]["DATABASE_URL"] for job in workflow["jobs"].values()
            for step in job["steps"] if "DATABASE_URL" in step.get("env", {})]  # fmt: skip
    assert urls
    for url in urls:
        check_test_host(url.replace("postgresql+psycopg://", "postgresql://"))


def test_alembic_refuses_a_database_url_that_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", NEON)
    with pytest.raises(SystemExit, match=r"points at ep-example-123.*trialpulse.live.migrate"):
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")


@pytest.mark.parametrize(
    "url",
    ["postgresql://someone:secret@ep-example-123-pooler.eu-central-1.aws.neon.tech/neondb",
     "mysql://someone:secret@example.org/db", "not a url"],
)  # fmt: skip
def test_migrate_refuses_a_pooled_or_foreign_url(url: str) -> None:
    with pytest.raises(RefusedError):
        migrate.upgrade(url)


def test_migrate_upgrades_the_explicit_url_and_never_reads_database_url(
    scratch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    command.downgrade(Config(str(REPO_ROOT / "alembic.ini")), "0005")  # DATABASE_URL: local
    monkeypatch.setenv("DATABASE_URL", NEON)  # would be refused, and is never read
    explicit = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    assert migrate.upgrade(explicit) == ("0005", "0007")
    assert migrate.upgrade(explicit) == ("0007", "0007")


def _secrets_with(monkeypatch: pytest.MonkeyPatch, url: str | None) -> None:
    from pydantic import SecretStr

    import trialpulse.config as config

    class Fake:
        neon_database_url = SecretStr(url) if url is not None else None

    monkeypatch.setattr(config, "Secrets", lambda: Fake())


def test_from_env_reads_neon_database_url_and_never_prints_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(migrate, "upgrade", lambda url: seen.append(url) or ("0005", "0006"))
    monkeypatch.setattr(migrate.getpass, "getpass", lambda *a: pytest.fail("prompted"))
    _secrets_with(monkeypatch, NEON)
    assert migrate.main(["--from-env"]) == 0
    assert seen == [NEON]
    printed = capsys.readouterr().out
    assert printed.strip() == "migrated from revision 0005 to 0006"
    assert "secret" not in printed


def test_from_env_refuses_a_pooled_url_and_a_missing_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets_with(monkeypatch, NEON.replace("ep-example-123.", "ep-example-123-pooler."))
    with pytest.raises(RefusedError, match="pooled"):
        migrate.main(["--from-env"])
    _secrets_with(monkeypatch, None)
    with pytest.raises(RefusedError, match="not set"):
        migrate.main(["--from-env"])


def test_the_prompt_stays_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrate.getpass, "getpass", lambda *a: "prompted-url")
    seen: list[str] = []
    monkeypatch.setattr(migrate, "upgrade", lambda url: seen.append(url) or (None, "0006"))
    assert migrate.main([]) == 0
    assert seen == ["prompted-url"]
