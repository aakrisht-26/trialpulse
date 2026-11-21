# How TrialPulse compares with published work

Published studies have predicted from ClinicalTrials.gov whether a trial will stop early. This page sets four of them beside TrialPulse: the data, the setup, the metric and the result as each reports it, and how its setup differs. All four are peer reviewed and open access, and each was read in full text for this page on 2026-11-22. Numbers are quoted as the papers print them. Two candidates were left out: one whose full text could not be read (so its numbers could not be checked), and one preprint that predicts trial success rather than termination.

**The short answer.** The numbers below are not comparable with TrialPulse's, and this page does not claim that TrialPulse beats any of them. The studies answer a different question on a different population, under a different split:

- **The question.** Three of the four classify trials that have already ended (completed against terminated, or against ceased) as of the download date. TrialPulse estimates, at registration and every 6 months after it, the probability of an early stop within the next 12 and 24 months, for every open trial, and ranks trials that may still complete.
- **The data each feature reads.** No study states that its features were taken as of a date before the outcome; each uses the records as downloaded, after the trials it classifies had ended. TrialPulse rebuilds every trial from its version history as it stood on the prediction date ([features.md](features.md)), because a record is rewritten when a trial stops.
- **The split.** The studies test on a random or cross-validated share of the same years they train on. TrialPulse trains on landmarks before an origin and tests on the year after it, with the test origins locked behind a registration made before they were scored ([preregistration.md](preregistration.md), [results.md](results.md)).
- **Open trials and competing events.** The classifiers drop trials that have not ended. The one survival study censors them but treats completion as its event, with no competing risk. TrialPulse counts open trials as censored, treats completion as a competing event, and weights its metrics for censoring (IPCW).

TrialPulse's primary result, for comparison of setups rather than of numbers: an IPCW time-dependent AUC for an early stop within 12 months of 0.730 (0.719 to 0.741) at the 2018 test origin and 0.723 (0.712 to 0.734) at 2019 ([results.md](results.md)). It is numerically close to the first study's and below the second's. Both differences mean little, because the tasks differ: an AUC on ended trials separates completed from terminated records after the fact, while TrialPulse's ranks open trials by what was known on the prediction date, against outcomes that had not happened yet.

## The studies

### 1. Elkin and Zhu (2021), Scientific Reports

Elkin ME, Zhu X. Predictive modeling of clinical trial terminations using feature engineering and embedding learning. *Scientific Reports* 11, 3446 (2021). [doi:10.1038/s41598-021-82840-x](https://doi.org/10.1038/s41598-021-82840-x), full text at [PMC7876037](https://pmc.ncbi.nlm.nih.gov/articles/PMC7876037/).

- **Data.** 311,260 trials "downloaded from ClinicalTrials.gov in May 2019"; kept "Completed or Terminated trials, starting in or after 2000, belonging to one of the top 25 countries"; a testbed of 68,999 trials, 11.46% terminated. Withdrawn and suspended trials, and trials still open, are not in the testbed.
- **Setup.** Binary classification, "Completed" against "Terminated", from 640 features (administrative, study design, eligibility, keywords and a Doc2Vec embedding of the description). Five-fold cross-validation with a 20% hold-out; the paper does not say the split follows time. Keyword features were ranked by TF-IDF "using all (68,999) clinical trials". No censoring or competing risks.
- **Result as reported.** Ensemble XGBoost with random under-sampling: AUC 73.01% and balanced accuracy 67.20% (Table 4); "over 0.73 AUC and over 67% Balanced Accuracy".
- **Key difference.** An ended-trials classification from the record as downloaded, under a split that is not shown to follow time. TrialPulse's 12-month AUC is about the same number on a different task, so the two say nothing about each other.

### 2. Elkin and Zhu (2021), PLoS ONE, COVID-19 trials

Elkin ME, Zhu X. Understanding and predicting COVID-19 clinical trial completion vs. cessation. *PLoS ONE* 16(7): e0253789 (2021). [doi:10.1371/journal.pone.0253789](https://doi.org/10.1371/journal.pone.0253789).

- **Data.** 4,441 COVID-19 trials "downloaded, in XML format, from ClinicalTrials.gov in January, 2021"; kept trials marked Completed, or Terminated, Withdrawn or Suspended ("cessation trials"): 772 trials, 18.65% cessation.
- **Setup.** Binary classification of completion against cessation, with random under-sampling of completed trials. "The dataset is separated into five subsets of training and test samples for 5-fold cross validation"; feature selection used training data only. No censoring or competing risks.
- **Result as reported.** "more than 0.87 AUC (Area Under the Curve) score and 0.81 balanced accuracy"; in the text, "random forest with top 600 features has AUC score of 87.58%".
- **Key difference.** A small, single-disease population of 772 ended trials from one pandemic year, cross-validated within that year. TrialPulse's AUC is lower, on all open interventional trials and a time-based test, and the two are not comparable.

### 3. Geletta, Follett and Laugerman (2019), BMC Medical Informatics and Decision Making

Geletta S, Follett L, Laugerman M. Latent Dirichlet Allocation in predicting clinical trial terminations. *BMC Medical Informatics and Decision Making* 19, 242 (2019). [doi:10.1186/s12911-019-0973-y](https://doi.org/10.1186/s12911-019-0973-y), full text at [PMC6882341](https://pmc.ncbi.nlm.nih.gov/articles/PMC6882341/).

- **Data.** 252,847 studies from the Clinical Trials Transformation Initiative (AACT) "through September 29, 2017"; kept studies that started before May 1, 2015 "in order to give the trials time to terminate or complete" and that were completed or terminated: 119,591 studies.
- **Setup.** A random forest on 25 topic probabilities from the brief summaries (Latent Dirichlet Allocation) and six structured variables, "randomly partitioning the data into 70% training and 30% model testing". No censoring or competing risks.
- **Result as reported.** No AUC is printed. From the ROC curve of the full model: "a sensitivity of 0.6 while still maintaining a fairly low 1-specificity of 0.3"; with the structured variables alone, "unable to obtain a sensitivity greater than 0.05 for any reasonable threshold".
- **Key difference.** Ended trials only, a random split, and no single summary metric to set beside TrialPulse's. Its point, that the text of a trial carries signal its structured fields lack, is one TrialPulse tested with its own text components (TF-IDF and truncated SVD, not topics): on the development origins they lowered the 12-month AUC, so the served model leaves them out ([ADR 0026](adr/0026-the-champion-leaves-out-the-text-families.md)).

### 4. Qi, Yang, Zou and Hu (2024), Quantitative Biology

Qi H, Yang W, Zou W, Hu Y. A clinical trial termination prediction model based on denoising autoencoder and deep survival regression. *Quantitative Biology* 12(2): 205-214 (2024). doi:10.1002/qub2.43 (the publisher refuses automated link checks), full text at [PMC12806306](https://pmc.ncbi.nlm.nih.gov/articles/PMC12806306/).

- **Data.** 819 trials in pregnant women from ClinicalTrials.gov, "initiated on the website between 1 January 2009 and 31 December 2018", with 33 covariates.
- **Setup.** A survival model, the one study here with a time-to-event framing. "Time was defined as the period from the study start date to the study completion date or the date of the last posted update"; trials with the status "completed" are the event and other statuses are censored. No competing risk is modeled. "the dataset was randomly divided into a training set (80%) and a test set (20%)".
- **Result as reported.** "the C-index of the training and test datasets were 0.74 and 0.75 respectively" for the proposed model; 0.71 on the test set for a Cox proportional hazards model (Table 1).
- **Key difference.** It counts time from the trial's start and reads the last posted update, so its time variable depends on the record after the outcome, and it models completion rather than an early stop, with no competing risk. It reports a C-index, not an AUC at a horizon, on a random split of 819 trials. Not comparable with TrialPulse's numbers.

## What TrialPulse adds, and what it does not

TrialPulse's contribution is the setup, not a higher number: features rebuilt as of each prediction date from the version history, landmarks through a trial's life, competing risks with censoring-weighted evaluation, and a time-based test registered before it was run, with three of its five hypotheses falsified and published ([results.md](results.md)). Its 12-month AUC is close to Elkin and Zhu's on ended trials from the downloaded record, and below the COVID-19 study's. A study with a higher number in an easier setup does not show that TrialPulse would score as high in that setup, and TrialPulse's numbers do not show that it would beat those studies in theirs. Neither setup was re-run here.
