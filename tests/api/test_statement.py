"""The model statement of ADR 0048 is held to the documents it summarizes."""

import json
import re

from trialpulse.api.main import FLAG
from trialpulse.config import REPO_ROOT

STATEMENT = REPO_ROOT / "docs" / "model_statement.json"
SOURCES = ("docs/results.md", "docs/model_card.md")


def test_every_number_of_the_statement_is_in_the_results_or_the_model_card() -> None:
    text = STATEMENT.read_text(encoding="utf-8")
    sources = " ".join((REPO_ROOT / s).read_text(encoding="utf-8") for s in SOURCES)
    text = re.sub(r"ADR \d{4}", "", text)  # references, not results
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    assert len(numbers) > 60
    missing = sorted({n for n in numbers if n.lstrip("+-") not in sources})
    assert missing == [], missing


def test_the_statement_names_the_registered_results_and_the_flag() -> None:
    statement = json.loads(STATEMENT.read_text(encoding="utf-8"))
    origins = statement["registered_results"]["origins"]
    assert set(origins) == {"2018", "2019"}
    assert {"auc_12", "auc_24", "brier_12", "lift_12", "calibration_slope_24"} <= set(
        origins["2018"]
    )
    assert origins["2018"]["auc_12"] == {"value": 0.7303, "low": 0.7191, "high": 0.7414}
    assert [h["id"] for h in statement["falsified_hypotheses"]] == ["H1", "H3", "H4"]
    assert statement["served_scope"]["flag"] == FLAG
    assert "overstate" in statement["served_scope"]["flag_note"]
