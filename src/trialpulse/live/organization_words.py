"""The organization words of the display-name allowlist (ADR 0059).

A sponsor name is shown when it holds one of these words (or under the other two conditions
of ADR 0059). Matching runs on the sponsor key (`contracts.sponsor.sponsor_key`: NFKC,
lowercase, punctuation as spaces), word by word:

- `PREFIXES`: a key word that starts with one of these is an organization word. Stems are long
  enough not to open common surnames or given names ("medic" would open "Medici", so the
  full words are listed instead).
- `WORDS`: a key word equal to one of these (legal forms and well-known abbreviations, too
  short to use as prefixes). Short forms that are also names or initials ("Ab", "Co", "Sa",
  "Se", "Va") are left out; "Co." and "S.A." with their dots are in `DOTTED`.
- `DOTTED`: legal forms written with dots or a slash ("S.A.", "B.V.", "A/S"), matched on the
  name itself, since the key splits them into single letters that initials also give.

The list errs toward hiding: a name of an organization that holds none of these words is
hidden unless it is one word or a large sponsor of a public or industry class. Adding a
word is a reviewed change to this file, with a test.
"""

import re

PREFIXES: tuple[str, ...] = (
    # English
    "academ", "agency", "alliance", "association", "authority", "biolog", "biomed",
    "biopharm", "bioscien", "biotech", "board", "bureau", "cancer", "center",
    "centre", "children", "clinic", "coalition", "colleg", "commission",
    "committee", "compan", "consort", "consult", "cooperat", "collaborat", "corporat",
    "council", "county", "dental", "department", "diagnost", "division", "enterprise",
    "faculty", "federa", "foundation", "genomic", "global", "government", "group",
    "health", "holding", "hospice", "hospital", "incorporat", "industr", "infirmary",
    "initiative", "institu", "international", "laborator", "league", "limited",
    "medical", "medicine", "military", "ministry", "national", "network", "nursing",
    "office", "oncolog", "organi", "paediatric", "partners", "pediatric", "pharma",
    "polyclin", "program", "project", "regional", "registry", "research", "scien",
    "school", "service", "society", "solution", "system", "technolog", "therapeut",
    "trial", "trust", "union", "unit", "universi", "vaccin", "veteran", "worldwide",
    # Spanish and Portuguese
    "asociaci", "associaç", "centro", "clínic", "complejo", "consejo", "consorcio",
    "departamento", "escuela", "estudio", "faculdade", "facultad", "farmac", "fundaç",
    "fundac", "gobierno", "grupo", "hospitalar", "instituto", "internacional",
    "laboratório", "medicina", "ministerio", "nacional", "proyecto", "salud", "saúde",
    "servicio", "sociedad", "sociedade", "universidad", "universidade",
    # French
    "assistance", "association", "centre", "clinique", "conseil", "département",
    "école", "ecole", "faculté", "fondation", "groupe", "hôpita", "hopita", "laboratoire",
    "médical", "ministère", "recherche", "santé", "société", "societe", "unicancer",
    # Italian
    "agenzia", "associazione", "azienda", "consiglio", "consorzio", "dipartimento",
    "fondazione", "gruppo", "istitut", "nazionale", "ospedal", "policlin", "progetto",
    "regione", "sanit", "scuola", "servizio", "società", "studio", "universit",
    # German
    "akadem", "forschung", "gesellschaft", "gesundheit", "hochschule", "institut",
    "klinik", "krankenhaus", "medizin", "stiftung", "verein", "zentrum",
    # Dutch
    "centrum", "medisch", "stichting", "universiteit", "vereniging", "ziekenhuis",
    # Nordic
    "kommune", "landsting", "region", "sairaala", "sjukhus", "sygehus", "sykehus",
    "universitet", "yliopisto",
    # Polish, Czech and Hungarian
    "egyetem", "fakult", "fundacja", "instytut", "klinika", "nemocnic", "szpital",
    "univerz", "uniwersytet",
    # Turkish
    "araştırma", "arastirma", "eğitim", "egitim", "hastane", "üniversite",  # noqa: RUF001
    "universite",
)  # fmt: skip

WORDS: frozenset[str] = frozenset(
    {
        # Legal forms
        "ag", "aps", "as", "asa", "bhd", "bv", "corp", "gmbh", "inc", "jsc", "kft",
        "kg", "kk", "llc", "llp", "ltd", "ltda", "nv", "oy", "oyj", "plc", "pjsc", "pte",
        "pty", "pvt", "sab", "sae", "sarl", "sas", "sdn", "sl", "slu", "spa", "sro",
        "srl", "tbk",
        # Well-known abbreviations of public bodies and hospital trusts
        "aou", "asl", "asst", "ausl", "care", "chu", "chru", "irccs", "lab", "labs", "nhs",
        "nih", "nci", "umc",
    }
)  # fmt: skip

DOTTED = re.compile(
    r"(?i)(?<![\w.])(s\.a\.(?:s\.)?|s\.p\.a\.|s\.r\.l\.|s\.l\.|b\.v\.|n\.v\.|a/s|e\.v\.|"
    r"l\.l\.c\.|p\.l\.c\.|k\.k\.|co\.|inc\.|ltd\.)(?![\w])"
)
