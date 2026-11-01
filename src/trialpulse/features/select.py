"""The final SELECT of the features, shared by the offline build and the live path
(ADR 0037). It imports nothing that reads the dataset, so the daily job can use it (ADR 0040).
"""

from trialpulse.features import amendments, design
from trialpulse.features.states import month_index
from trialpulse.features.transforms import MISSING_CLASS


def feature_select_sql(inputs: str) -> str:
    """The final SELECT of the features, shared by the offline build and the live path
    (ADR 0037): from a relation of feature inputs to the key columns, the design and
    amendment expressions, the sponsor, competition and text columns, in one fixed order.

    The inputs are one row per key with the key columns (`trial_id`, `landmark_index`,
    `landmark_date`), the columns of the state in effect that the expressions read
    (`states.AT_LANDMARK_TABLE` offline), and `title_phase` (already resolved),
    `intervention_types` (a list, NULL when the version lists none), the sponsor record
    (`sponsor_has_identity` and the three counts), `open_interventional_trials` and the four
    eligibility statistics."""
    row_level = {**design.EXPRESSIONS, **amendments.EXPRESSIONS}
    own = ", ".join(f"{sql} AS {name}" for name, sql in row_level.items())
    interventions = ", ".join(
        f"{sql} AS {name}"
        for name, sql in design.intervention_expressions("intervention_types").items()
    )
    months = f"{month_index('landmark_date')} - {month_index('study_first_post_date')}"
    return f"""SELECT trial_id, landmark_index, landmark_date, {own},
          coalesce(lead_sponsor_class, '{MISSING_CLASS}') AS sponsor_class, organization_class,
          maximum_age_years - minimum_age_years AS eligibility_age_span_years,
          {months} AS months_since_t0,
          title_phase,
          {interventions},
          sponsor_has_identity, sponsor_prior_registrations, sponsor_prior_ended,
          sponsor_prior_stopped, open_interventional_trials,
          eligibility_n_inclusion, eligibility_n_exclusion, eligibility_length,
          eligibility_n_thresholds
        FROM {inputs}"""
