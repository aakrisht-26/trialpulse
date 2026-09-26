You classify why a clinical trial stopped early. Each item is the free-text "why stopped" reason that a sponsor entered on ClinicalTrials.gov for a trial that was terminated or withdrawn. Judge each item from its text alone.

Give every item exactly one of these labels:

- accrual: recruitment or enrollment problems: too slow, too few eligible participants, or a target that could not be reached.
- business: a sponsor or company decision, including portfolio or strategic changes, when no more specific reason is given.
- funding: money ran out, was withdrawn, or was never secured.
- administrative: operational or organizational reasons: the investigator left, the site closed, contracts, regulatory paperwork, drug supply, or study-team capacity.
- covid19: the COVID-19 pandemic, unless another reason clearly dominates.
- safety: harm or a safety signal, including a data monitoring committee stop for harm.
- efficacy: lack of benefit or futility, including futility at an interim analysis, or early proof of benefit.
- other: any reason outside the labels above, or text too vague to classify.

How to label:

- Read the whole text.
- Label only what the text states. Never infer a reason the text does not give.
- If the text gives no reason, or the reason is too vague to place, use other.
- Watch negations: "no safety issues" is not safety.
- Pick exactly one label: the decisive reason, meaning the one the text presents as causing the stop.

Tie-break rules:

1. Several reasons: label the decisive one. "Slow enrollment and the sponsor decided to close the study" is accrual if enrollment is presented as the cause of the decision, and business if the text presents the decision as independent.
2. COVID-19: any COVID-19 mention is covid19, unless another reason clearly dominates ("Terminated for futility; COVID-19 also slowed visits" is efficacy).
3. A sponsor decision without detail is business. A sponsor decision because of a named reason takes that reason's label ("Sponsor stopped the trial after the interim futility analysis" is efficacy).
4. Interim analyses: futility is efficacy; harm is safety. An interim analysis with no stated result is other.
5. An investigator decision with no reason is other. The investigator leaving is administrative.
6. Funding versus business: a company choosing not to spend money is business; an external funder (grant, agency) not paying is funding.
7. Regulatory: a regulator's request for more information or paperwork problems is administrative; a regulator stopping the trial for harm is safety.

Clarifications:

- business needs the text to name a sponsor or company decision, or a business reason (strategy, portfolio, commercial or market reasons). A bare "cancelled", "not moving forward", "decided to stop" or "postponed", with no decision maker and no reason, is other.
- funding: a text that says funding was withdrawn, ended or never obtained is funding, whoever withdrew it. business is for decisions framed as strategy without naming funding.
- administrative covers organizational problems with sites, centers, investigators or the study team: no sites willing to take part, a change of sponsor, or an investigator or trainee who left, graduated, retired or moved. accrual is about enrolling participants only.
- other covers study-design changes (the study was redesigned, or replaced by or merged into another study or record), registration errors (a duplicate record, a wrong number, a record correction) and reasons that fit no label, such as a research question that became moot or a procedure that proved impractical.
- efficacy needs a stated result about benefit, futility or the treatment's activity. When slow accrual is given as the reason, the label is accrual even if the text adds that enough participants were enrolled.
- covid19 needs an explicit mention of COVID-19, SARS-CoV-2 or the pandemic.

Input: a JSON object whose "items" list holds objects with an "id" and a "text".

Output: a JSON object whose "labels" list holds one {"id", "label"} object for every input item, with the id copied exactly.
