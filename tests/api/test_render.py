"""The Render Blueprint (render.yaml, ADR 0065) and the image it builds: no secret value in
the file, the health check on /health, the port Render routes to, and a build context that
never holds data/ or .env (ADR 0040)."""

import re
from pathlib import Path

import yaml

from trialpulse.config import REPO_ROOT

BLUEPRINT = REPO_ROOT / "render.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile.api"


def _service() -> dict[str, object]:
    spec = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    services = spec["services"]
    assert len(services) == 1
    service: dict[str, object] = services[0]
    return service


def test_the_blueprint_builds_the_api_image_with_its_health_check() -> None:
    service = _service()
    assert service["type"] == "web"
    assert service["runtime"] == "docker"
    assert service["plan"] == "free"
    assert service["healthCheckPath"] == "/health"
    assert (REPO_ROOT / str(service["dockerfilePath"])).resolve() == DOCKERFILE.resolve()
    assert service["dockerContext"] == "."


def test_the_database_url_is_asked_for_and_never_written() -> None:
    env = {e["key"]: e for e in _service()["envVars"]}  # type: ignore[attr-defined]
    assert env["API_DATABASE_URL"] == {"key": "API_DATABASE_URL", "sync": False}
    text = BLUEPRINT.read_text(encoding="utf-8")
    assert not re.search(r"postgres(ql)?(\+\w+)?://", text)
    assert "password" not in text.lower()


def test_the_image_listens_on_the_port_render_routes_to() -> None:
    env = {e["key"]: e for e in _service()["envVars"]}  # type: ignore[attr-defined]
    port = env["PORT"]["value"]
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"PORT={port}" in dockerfile
    assert f"EXPOSE {port}" in dockerfile
    assert "--host 0.0.0.0 --port ${PORT}" in dockerfile


def test_the_build_context_holds_no_data_and_no_secret() -> None:
    """The .dockerignore is an allowlist: everything is left out, then only the files the
    image needs are let back in."""
    lines = [line.strip() for line in (REPO_ROOT / ".dockerignore").read_text(
        encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]  # fmt: skip
    assert lines[0] == "*"
    allowed = {line[1:].rstrip("/") for line in lines if line.startswith("!")}
    assert allowed == {"pyproject.toml", "uv.lock", "config", "src"}
    copied = re.findall(r"^COPY (.+) \S+$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert all(Path(p.rstrip("/")).parts[0] in allowed for c in copied for p in c.split())
