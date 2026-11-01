"""The served champion's bundle on disk: its files and how it is loaded (CLAUDE.md Step 13).
Nothing here reads the dataset, so the daily job loads the champion with it (ADR 0040);
`trialpulse.scoring.champion` fits and registers it.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import lightgbm as lgb

from trialpulse.cli import RefusedError
from trialpulse.config import REPO_ROOT, ProjectConfig
from trialpulse.features.transforms import SponsorPriors
from trialpulse.models import discrete_time
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring.predict import Bundle

CHAMPION_DIR = REPO_ROOT / "data" / "scoring" / "champion"


MODEL_NAME = "trialpulse-m4"


BOOSTER, LEVELS, PRIORS, META = "model.txt", "levels.json", "sponsor_priors.json", "meta.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_bundle(cfg: ProjectConfig, folder: Path = CHAMPION_DIR) -> Bundle:
    """The served champion from its folder, refused if a file is not the one recorded."""
    meta_path = folder / META
    if not meta_path.is_file():
        raise RefusedError(
            f"{meta_path} not found: fit the served champion with: "
            "uv run python -m trialpulse.scoring.champion"
        )
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    for name, digest in meta["files"].items():
        if sha256_of(folder / name) != digest:
            raise RefusedError(f"{folder / name} is not the file the champion was saved with")
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.booster = lgb.Booster(model_file=str(folder / BOOSTER))
    if model.booster.feature_name() != list(model.columns):
        raise RefusedError(
            f"the trees in {folder / BOOSTER} read other columns than M4 reads now; fit the "
            "served champion again"
        )
    levels = json.loads((folder / LEVELS).read_text(encoding="utf-8"))
    model.levels = {name: tuple(values) for name, values in levels.items()}
    model.summary = meta.get("summary", {})
    priors = SponsorPriors.from_json(json.loads((folder / PRIORS).read_text(encoding="utf-8")))
    return Bundle(model, priors, MODEL_NAME, str(meta.get("version", meta["files"][BOOSTER][:12])),
                  meta)  # fmt: skip
