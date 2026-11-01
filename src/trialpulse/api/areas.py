"""Areas of the landscape (ADR 0046): top-level MeSH disease categories and Mental Disorders.

A trial's areas are the categories below whose descriptor is among the MeSH ancestor terms of
its current record (API v2 `derivedSection.conditionBrowseModule.ancestors`). A trial can be
in several areas, or in none ("Not classified", code 0). Areas are display data from the
current record: no feature or score reads them (ADR 0007).

The descriptors are MeSH's top-level categories of tree C (C22, animal diseases, left out)
and F03. Their ids were checked against a bulk pull of the 222,417 live-status records of
API v2 on 2026-11-01: every one occurs except C21's, which a direct query found as D007280
on 17 records of the whole registry (the first list had a wrong id for it).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NOT_CLASSIFIED = 0


@dataclass(frozen=True)
class Area:
    code: int
    slug: str
    name: str
    mesh_id: str
    tree: str


AREAS: tuple[Area, ...] = (
    Area(1, "infections", "Infections", "D007239", "C01"),
    Area(2, "neoplasms", "Neoplasms", "D009369", "C04"),
    Area(3, "musculoskeletal", "Musculoskeletal Diseases", "D009140", "C05"),
    Area(4, "digestive", "Digestive System Diseases", "D004066", "C06"),
    Area(5, "stomatognathic", "Stomatognathic Diseases", "D009057", "C07"),
    Area(6, "respiratory", "Respiratory Tract Diseases", "D012140", "C08"),
    Area(7, "otorhinolaryngologic", "Otorhinolaryngologic Diseases", "D010038", "C09"),
    Area(8, "nervous-system", "Nervous System Diseases", "D009422", "C10"),
    Area(9, "eye", "Eye Diseases", "D005128", "C11"),
    Area(10, "urogenital", "Urogenital Diseases", "D000091642", "C12"),
    Area(11, "female-urogenital-pregnancy",
         "Female Urogenital Diseases and Pregnancy Complications", "D005261", "C13"),
    Area(12, "cardiovascular", "Cardiovascular Diseases", "D002318", "C14"),
    Area(13, "hemic-lymphatic", "Hemic and Lymphatic Diseases", "D006425", "C15"),
    Area(14, "congenital-hereditary-neonatal",
         "Congenital, Hereditary, and Neonatal Diseases and Abnormalities", "D009358", "C16"),
    Area(15, "skin-connective-tissue", "Skin and Connective Tissue Diseases", "D017437", "C17"),
    Area(16, "nutritional-metabolic", "Nutritional and Metabolic Diseases", "D009750", "C18"),
    Area(17, "endocrine", "Endocrine System Diseases", "D004700", "C19"),
    Area(18, "immune-system", "Immune System Diseases", "D007154", "C20"),
    Area(19, "environmental-origin", "Disorders of Environmental Origin", "D007280", "C21"),
    Area(20, "pathological-conditions", "Pathological Conditions, Signs and Symptoms",
         "D013568", "C23"),
    Area(21, "occupational", "Occupational Diseases", "D009784", "C24"),
    Area(22, "chemically-induced", "Chemically-Induced Disorders", "D064419", "C25"),
    Area(23, "wounds-injuries", "Wounds and Injuries", "D014947", "C26"),
    Area(24, "mental-disorders", "Mental Disorders", "D001523", "F03"),
)  # fmt: skip
BY_SLUG: Mapping[str, Area] = {a.slug: a for a in AREAS}
BY_CODE: Mapping[int, Area] = {a.code: a for a in AREAS}
_BY_MESH: Mapping[str, int] = {a.mesh_id: a.code for a in AREAS}
NOT_CLASSIFIED_SLUG = "not-classified"
NOTE = (
    "Areas come from the MeSH terms of each trial's current record, not from its history, and "
    "no score reads them. A trial counts in every area it belongs to, so area totals can "
    "exceed the number of trials."
)


def area_codes(ancestors: Iterable[Mapping[str, Any]] | None) -> list[int]:
    """The sorted area codes of a record's ancestor terms (empty when none is listed)."""
    found = {_BY_MESH[a["id"]] for a in ancestors or () if isinstance(a, Mapping)
             and a.get("id") in _BY_MESH}  # fmt: skip
    return sorted(found)


def slugs(codes: Sequence[int]) -> list[str]:
    return [BY_CODE[c].slug for c in codes if c in BY_CODE] or [NOT_CLASSIFIED_SLUG]
