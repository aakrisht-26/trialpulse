"""The API reads the serving tables only (Step 15): its modules load no model, no dataset
client and no offline code, and the image's dependency group holds none of them."""

import re
import shutil
import subprocess
import sys

import pytest

from trialpulse.config import REPO_ROOT

FORBIDDEN = ("lightgbm", "duckdb", "pandas", "numpy", "sklearn", "huggingface_hub", "mlflow",
             "trialpulse.live", "trialpulse.scoring", "trialpulse.features.build",
             "trialpulse.warehouse", "trialpulse.ingest")  # fmt: skip


def test_the_api_loads_no_model_dataset_or_offline_module() -> None:
    code = (
        "import sys, trialpulse.api.main; "
        f"print(','.join(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r} "
        f"or any(m == f or m.startswith(f + '.') for f in {FORBIDDEN!r})))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, cwd=REPO_ROOT)  # fmt: skip
    assert out.stdout.strip() == ""


def test_the_image_installs_the_api_group_only() -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    out = subprocess.run(
        [uv, "export", "--frozen", "--no-hashes", "--no-header", "--no-emit-project",
         "--only-group", "api", "--format", "requirements-txt"],
        capture_output=True, text=True, check=True, cwd=REPO_ROOT,
    )  # fmt: skip
    names = {re.split(r"[=<>; \[]", line, maxsplit=1)[0].lower()
             for line in out.stdout.splitlines() if line and line[0].isalnum()}  # fmt: skip
    assert {"fastapi", "uvicorn", "psycopg", "sqlalchemy", "pydantic-settings"} <= names
    assert not names & {"lightgbm", "duckdb", "pandas", "numpy", "scikit-learn",
                        "huggingface-hub", "mlflow-skinny"}  # fmt: skip
    dockerfile = (REPO_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    assert "--only-group api" in dockerfile
