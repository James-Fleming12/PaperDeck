from __future__ import annotations

import re
from dataclasses import dataclass

from .fields import Category

THEORY_KEYWORDS: dict[str, int] = {
    "theorem": 4,
    "proof": 3,
    "lemma": 3,
    "corollary": 3,
    "proposition": 2,
    "bound": 2,
    "upper bound": 2,
    "lower bound": 2,
    "convergence": 2,
    "regret": 3,
    "sample complexity": 4,
    "pac-bayes": 3,
    "generalization bound": 3,
    "analysis": 1,
    "asymptotic": 2,
    "closed form": 2,
    "worst-case": 2,
    "approximation guarantee": 3,
    "we prove": 4,
    "is np-hard": 3,
    "polynomial time": 2,
    "exact algorithm": 1,
    "dual": 1,
    "optimal rate": 2,
}

EMPIRICAL_KEYWORDS: dict[str, int] = {
    "experiment": 3,
    "experiments": 3,
    "dataset": 3,
    "datasets": 3,
    "benchmark": 3,
    "evaluation": 2,
    "empirical": 2,
    "ablation": 3,
    "outperform": 3,
    "state-of-the-art": 2,
    "accuracy": 2,
    "f1 score": 3,
    "we train": 3,
    "pretrained": 2,
    "fine-tune": 2,
    "baseline": 2,
    "implementation": 1,
    "hyperparameter": 2,
    "on the test set": 3,
    "results show": 2,
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


@dataclass
class Classification:
    label: str
    theory_score: float
    empirical_score: float
    venue_signal: str | None
    has_abstract: bool


def _keyword_score(text: str, keywords: dict[str, int]) -> float:
    score = 0.0
    for kw, weight in keywords.items():
        if " " in kw or "-" in kw:
            score += weight * len(re.findall(re.escape(kw), text))
        else:
            score += weight * len(re.findall(rf"\b{re.escape(kw)}\b", text))
    return score


def _venue_signal(venue: str | None, category: Category | None) -> str | None:
    if not venue:
        return None
    v = venue.lower()
    if category and any(tv in v for tv in category.theory_venues):
        return "theory"
    if category and any(ev in v for ev in category.empirical_venues):
        return "empirical"
    return None


def classify(
    title: str | None,
    abstract: str | None,
    venue: str | None = None,
    category: Category | None = None,
) -> Classification:
    title_text = (title or "").lower()
    abstract_text = (abstract or "").lower()
    has_abstract = bool(abstract_text.strip())

    theory = _keyword_score(title_text, THEORY_KEYWORDS) * 2.0
    empirical = _keyword_score(title_text, EMPIRICAL_KEYWORDS) * 2.0
    theory += _keyword_score(abstract_text, THEORY_KEYWORDS)
    empirical += _keyword_score(abstract_text, EMPIRICAL_KEYWORDS)

    signal = _venue_signal(venue, category)
    if signal == "theory":
        theory += 8.0
    elif signal == "empirical":
        empirical += 8.0

    if not has_abstract:
        theory *= 0.6
        empirical *= 0.6

    if signal is None and max(theory, empirical) < 3.0:
        label = "mixed"
    elif theory > empirical:
        label = "theoretical"
    elif empirical > theory:
        label = "empirical"
    else:
        label = "mixed"

    return Classification(
        label=label,
        theory_score=round(theory, 3),
        empirical_score=round(empirical, 3),
        venue_signal=signal,
        has_abstract=has_abstract,
    )
