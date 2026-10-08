# 0021. Conventions of the point-in-time features

- Date: 2026-10-07
- Status: decisions 1 and 2 are **accepted** by Aakrisht (Step 5 review of 2026-10-07, open questions 3 and 4). Decisions 3 to 8 are Claude's while building Step 7 and are **proposed**, listed for his review in the Step 7 report.
- Refines CLAUDE.md Section 8 (feature rules and families) and Section 6 ("Fitted transforms").

## Context

Section 8 lists the feature families and the rule that a feature at landmark L uses only versions posted on or before L. Building them raised questions the section does not settle: how dates of different precision are compared, what an enrollment count means, which rows get features, what "fit on each origin's training rows" means for each fitted quantity, and which fields may be read at all.

## Decision

1. **Calendar months** (accepted). The registry gave start and completion dates to the month through 2016 and mostly to the day from 2017 (`docs/eda.md`, finding 5). Every feature that compares dates compares calendar months: durations, slips against the first version, the registration lag, the overdue flags, and the months since the last update or status verification. A trial entered with day precision and the same trial entered with month precision get the same features. Counts of versions use post dates as they are: the version clock has always been a day. One feature depends on a date's precision, and on purpose: the competition count leaves out lapsed records, and the lapse rule of ADR 0014 takes a completion date at the end of its precision period. That is the cohort's own definition of an open trial, applied to every trial alike.
2. **Enrollment** (accepted). An enrollment count is a target while its type is ESTIMATED and the number enrolled once it is ACTUAL (`docs/eda.md`, finding 4). The "enrollment target change ratio" of Section 8 is therefore two features: `enrollment_target_ratio` while both the current and the first count are targets, and `enrolled_to_first_target_ratio` once enrollment has closed. `enrollment_closed` says which applies, and belongs to the amendment family.
3. **One row per landmark, whatever the cohort.** Features are computed for the landmark rows of the final cohort and of the cohort as of each walk-forward origin (ADR 0016) together: 1,511,335 rows. A feature at L depends on the registry up to L and on nothing else, so a row that exists in several cohorts has one set of features. A model's training matrix for origin T is the rows of the cohort as of T; its evaluation matrix is the final cohort's rows with T <= L < T + 12 months.
4. **On L or before L.** The state at L is the latest version posted on or before L (Section 6). The sponsor track record reads versions posted strictly before L (ADR 0018).
5. **What is fitted, and on what.** Two things are estimated from data and are fitted once per origin on the rows of the cohort as of that origin:
   - the class rates behind the sponsor's smoothed early-stop rate: per sponsor class, the share of early stops among the training trials that had ended by the origin, one row per trial, smoothed toward the rate of all classes with the same prior weight of 10;
   - the text components (ADR 0019), on the distinct texts of the training rows.

   The counts they are applied to are point-in-time already. The fitted results are stored per origin, and `trialpulse.features.frame.load` joins them to an origin's rows.
6. **The whitelist is a view.** The feature queries read a `versions` view that holds the allowed fields of the canonical schema and no other. Left out:
   - `why_stopped_hash`: label-only (Section 8 rule 3);
   - `effective_date_type` and `study_first_post_date_type`: the registry recorded post dates from 2017 on and estimated them before, so the type would stand in for calendar time;
   - `start_date_type`: recorded from 2017 on only (no version with a start date carries it through 2016, 53% of those posted in 2017, 99% of those posted in 2026);
   - `lead_sponsor_name`: a name;
   - `submitted_date` and `study_first_submit_date`: the version clock is the post date, when the public could see a version (Section 6), not the day it was submitted;
   - `start_date_precision` and `completion_date_type`: no feature reads them, and a field is on the whitelist only while a feature names it as a source;
   - `source` and `content_hash`: bookkeeping of the pipeline.

   The view also shows each version's submitted status in `overall_status` and nothing in `last_known_status`. The registry computes its UNKNOWN label when a record has gone unverified for two years, and the dataset shows the label on the record's last version: read at a landmark, it would say that no later version exists (ADR 0014). Two tests keep the lists honest: the allowed and the left-out lists cover the canonical schema together, so a new field must be put on one side on purpose, and every allowed field but the trial identifier is the declared source of a feature.
7. **Features beyond the list of Section 8,** each from fields on the whitelist:
   - `status`, the submitted status at the landmark, and `primary_completion_overdue`: the EDA found status and overdue dates to separate risk more than edits do (finding 4). A primary completion date the registry shows as ACTUAL has been reached, not missed, so the flag is true only for a date still anticipated whose month has ended, and `primary_completion_reached` says the registry shows it as done. Unlike the start date type, the primary completion date type is recorded in every year of the data;
   - `registration_lag_months` beside the retrospective-registration flag (finding 5);
   - `start_anticipated` in place of the registry's start date type, which Section 8 lists ("start date actual or anticipated"): the start date is anticipated while its month lies after the landmark's month. The registry's own field exists from 2017 on only (decision 6);
   - `sponsor_has_identity` (ADR 0018);
   - `title_phase` as a category with the registry's phase names, NONE and OTHER (ADR 0006);
   - the eligibility statistics counted by lines: the exclusion criteria start at the first line that holds the words "exclusion criteria".
   - `open_interventional_trials` counts the row's own trial and leaves out records the UNKNOWN rule has lapsed (ADRs 0007 and 0014).
8. **The recent window ends at the landmark and starts at the previous one.** "Versions in the last 6 months" (Section 8) counts versions posted after the first post plus 6(k - 1) months and on or before the landmark, by the calendar arithmetic that places the landmarks. Landmark days are clamped to the month (31 August plus 6 months is 28 February), so "the landmark minus 6 months" would count the versions of 29 to 31 August at two landmarks. With the spacing and the window both 6 months, each version is recent at exactly one landmark.

## Evidence

- `test_dates_are_compared_by_calendar_month_whatever_their_precision`: the same history entered to the day and to the month gives identical features.
- `test_a_landmark_in_the_middle_of_a_month_compares_months_not_days`: with a landmark on the 20th and dates a few days before and after it in the same month, each month-based feature gives the answer day arithmetic would not. The scripted test registry posts its trials on the 20th of January and the 31st of August, so no landmark of the leakage tests is a first of the month.
- `test_each_version_is_recent_at_one_landmark_also_when_a_landmark_day_is_clamped`, `test_a_primary_completion_date_that_is_actual_was_reached_not_missed`, `test_the_registrys_later_unknown_label_changes_no_feature`, `test_the_view_shows_the_submitted_status_and_never_the_registrys_later_label`, `test_every_whitelisted_field_is_the_source_of_a_feature`.
- `test_an_enrollment_count_is_a_target_until_enrollment_closes`.
- `test_features_at_a_landmark_ignore_every_later_version` and the same check on the real warehouse (`uv run python -m trialpulse.features.leakage`).
- `test_what_is_fitted_for_an_origin_depends_only_on_its_training_rows`.
- `test_features_read_only_whitelisted_fields`, `test_the_whitelist_names_real_fields_and_leaves_out_the_label`.

## Alternatives

- **Carry the date precision as a feature** instead of comparing months. The precision changes just before the locked test years, so it would stand in for calendar time, like the post-date type.
- **Compute features per origin.** Five copies of mostly the same rows, and a chance for the copies to differ. The point-in-time rule makes one copy enough, and the rewrite check proves the rule holds.
- **Class rates as of each landmark** (a running class-level rate) instead of a fitted prior. It would be point-in-time too, but Section 6 names smoothing priors among the fitted transforms.

## Consequences

- Month precision loses the day within the month. "Start overdue" turns true when the start month has ended, up to a month after the day itself.
- The fitted features of a row differ between origins by design. The point-in-time features do not.
- The five features of decision 7 that Section 8 does not list (`status`, `primary_completion_overdue`, `primary_completion_reached`, `registration_lag_months`, `sponsor_has_identity`) can be dropped in the Step 10 ablation if they add nothing.
