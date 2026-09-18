from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    subfield_ids: tuple[str, ...] = ()
    field_ids: tuple[str, ...] = ()
    theory_venues: frozenset[str] = field(default_factory=frozenset)
    empirical_venues: frozenset[str] = field(default_factory=frozenset)

    @property
    def openalex_filters(self) -> list[str]:
        filters: list[str] = []
        if self.subfield_ids:
            filters.append(f"primary_topic.subfield.id:{'|'.join(self.subfield_ids)}")
        if self.field_ids:
            filters.append(f"primary_topic.field.id:{'|'.join(self.field_ids)}")
        return filters

    def matches(self, subfield_id: str | None, field_id: str | None) -> bool:
        return (subfield_id in self.subfield_ids) or (field_id in self.field_ids)


THEORY_VENUES = frozenset(
    v.lower()
    for v in (
        "COLT",
        "FOCS",
        "STOC",
        "SODA",
        "ALT",
        "APPROX",
        "RANDOM",
        "ICALP",
        "PODC",
        "LICS",
        "CADE",
        "ITCS",
        "CCC",
        "SICOMP",
        "Annals of Mathematics",
        "Journal of the ACM",
        "SIAM Journal on Computing",
        "Theoretical Computer Science",
        "Mathematical Programming",
        "Journal of Machine Learning Research",
        "Foundations and Trends in Machine Learning",
        "Journal of the American Mathematical Society",
        "Inventiones Mathematicae",
        "Annals of Statistics",
    )
)

EMPIRICAL_VENUES = frozenset(
    v.lower()
    for v in (
        "CVPR",
        "ICCV",
        "ECCV",
        "WACV",
        "ACL",
        "EMNLP",
        "NAACL",
        "ICLR",
        "ICML",
        "NeurIPS",
        "KDD",
        "WWW",
        "SIGIR",
        "MM Conference",
    )
)

CATEGORIES: dict[str, Category] = {
    "ml_theory": Category(
        key="ml_theory",
        label="ML Theory",
        subfield_ids=("1702", "1703"),
        theory_venues=THEORY_VENUES,
        empirical_venues=EMPIRICAL_VENUES,
    ),
    "tcs": Category(
        key="tcs",
        label="Theoretical Computer Science",
        subfield_ids=("2614",),
        theory_venues=THEORY_VENUES,
        empirical_venues=EMPIRICAL_VENUES,
    ),
    "math": Category(
        key="math",
        label="Mathematics",
        field_ids=("26",),
        theory_venues=THEORY_VENUES,
    ),
    "rendering": Category(
        key="rendering",
        label="Rendering",
        subfield_ids=("1704",),
        theory_venues=THEORY_VENUES,
        empirical_venues=EMPIRICAL_VENUES,
    ),
}

TOP_INSTITUTION_NAMES = frozenset(
    n.lower()
    for n in (
        "Massachusetts Institute of Technology",
        "Stanford University",
        "Harvard University",
        "University of California, Berkeley",
        "Carnegie Mellon University",
        "Princeton University",
        "California Institute of Technology",
        "University of Oxford",
        "University of Cambridge",
        "ETH Zurich",
        "EPFL",
        "Tsinghua University",
        "Peking University",
        "University of Toronto",
        "University of Washington",
        "Cornell University",
        "Columbia University",
        "University of Chicago",
        "Yale University",
        "New York University",
        "University of California, Los Angeles",
        "University of California, San Diego",
        "Georgia Institute of Technology",
        "University of Illinois Urbana-Champaign",
        "University of Texas at Austin",
        "Max Planck Society",
        "National University of Singapore",
        "Nanyang Technological University",
    )
)


def get_category(key: str) -> Category:
    try:
        return CATEGORIES[key]
    except KeyError as exc:
        valid = ", ".join(sorted(CATEGORIES))
        raise KeyError(f"Unknown category '{key}'. Valid: {valid}") from exc
