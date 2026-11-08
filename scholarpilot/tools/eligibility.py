"""Rule-based eligibility engine.

The LLM never decides eligibility on its own. It calls this engine, which
checks every rule and returns an explanation for each one. Each criterion is
"pass", "fail" or "unknown" (the profile is missing the data or the rule needs a
human check). The overall verdict is:

  eligible          every checkable rule passes and nothing is unknown
  likely_eligible   no rule fails, but some are unknown
  not_eligible      at least one rule fails
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

ENGLISH_TESTS = {"ielts": "IELTS", "toefl_ibt": "TOEFL iBT", "pte": "PTE Academic", "duolingo": "Duolingo English Test"}


@dataclass
class Criterion:
    name: str
    status: str  # pass | fail | unknown
    detail: str
    fixable: bool = False  # True when the student can fix it (e.g. take a test)


@dataclass
class EligibilityResult:
    scholarship_id: str
    scholarship_name: str
    level: str
    verdict: str
    criteria: list[Criterion] = field(default_factory=list)
    manual_checks: list[str] = field(default_factory=list)
    catalog_status: str = "needs_review"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["summary"] = self.summary()
        return d

    def summary(self) -> str:
        fails = [c.detail for c in self.criteria if c.status == "fail"]
        unknown = [c.name for c in self.criteria if c.status == "unknown"]
        if self.verdict == "eligible":
            return "You meet every rule we can check."
        if self.verdict == "likely_eligible":
            return "No rule rules you out, but we still need: " + ", ".join(unknown) + "."
        return "Not eligible yet: " + " ".join(fails)


def _age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def next_deadline(deadline: dict[str, Any] | None, today: date | None = None) -> date | None:
    """Next occurrence of a yearly deadline on or after today. None when each university
    or course sets its own date (no month/day in the catalog)."""
    if not deadline or not deadline.get("month") or not deadline.get("day"):
        return None
    today = today or date.today()
    d = date(today.year, deadline["month"], deadline["day"])
    return d if d >= today else date(today.year + 1, deadline["month"], deadline["day"])


def _age_reference_date(rules: dict[str, Any], deadline: date | None, today: date) -> date:
    ref = rules.get("age_reference")
    base = deadline or today
    if not ref:
        return base
    year = base.year + ref.get("years_after_deadline", 0)
    return date(year, ref["month"], ref["day"])


def _profile_get(profile: dict[str, Any], key: str) -> Any:
    value = profile.get(key)
    return None if value in ("", None) else value


def cgpa_percent(profile: dict[str, Any]) -> float | None:
    cgpa = _profile_get(profile, "cgpa")
    if cgpa is None:
        return None
    scale = _profile_get(profile, "cgpa_scale") or 4.0
    return round(float(cgpa) / float(scale) * 100, 1)


def check_eligibility(
    scholarship: dict[str, Any], profile: dict[str, Any], level: str, today: date | None = None
) -> EligibilityResult:
    today = today or date.today()
    result = EligibilityResult(
        scholarship_id=scholarship["id"],
        scholarship_name=scholarship["name"],
        level=level,
        verdict="not_eligible",
        catalog_status=scholarship.get("verification_status", "needs_review"),
    )
    track = scholarship["tracks"].get(level)
    if track is None:
        result.criteria.append(
            Criterion("study level", "fail", f"This scholarship has no {level}'s track (offered for: {', '.join(scholarship['tracks'])}).")
        )
        return result

    rules = track["eligibility"]
    c = result.criteria

    # Nationality
    nationality = _profile_get(profile, "nationality") or "Pakistan"
    allowed = rules.get("nationality", "any")
    if allowed == "any":
        c.append(Criterion("nationality", "pass", "Open to all nationalities."))
    elif nationality.lower() in [n.lower() for n in allowed]:
        if rules.get("nationality_status") == "verify":
            c.append(Criterion("nationality", "unknown", f"{nationality} has been eligible before, but country lists change. Confirm on the official page."))
        else:
            c.append(Criterion("nationality", "pass", f"{nationality} is an eligible country."))
    else:
        c.append(Criterion("nationality", "fail", f"{nationality} is not on the eligible country list."))

    # Age
    deadline = next_deadline(track.get("deadline"), today)
    if rules.get("min_age") is not None or rules.get("max_age") is not None:
        dob_raw = _profile_get(profile, "date_of_birth")
        if dob_raw is None:
            c.append(Criterion("age", "unknown", "Add your date of birth so the age limit can be checked."))
        else:
            dob = date.fromisoformat(str(dob_raw))
            ref = _age_reference_date(rules, deadline, today)
            age = _age_on(dob, ref)
            lo, hi = rules.get("min_age"), rules.get("max_age")
            if lo is not None and hi is not None:
                limit = f"{lo}-{hi}"
            elif hi is not None:
                limit = f"up to {hi}"
            else:
                limit = f"at least {lo}"
            if (lo is not None and age < lo) or (hi is not None and age > hi):
                c.append(Criterion("age", "fail", f"You will be {age} on {ref.isoformat()}; the allowed age is {limit}."))
            else:
                c.append(Criterion("age", "pass", f"You will be {age} on {ref.isoformat()} (allowed: {limit})."))

    # Grades
    if level == "bachelor" and rules.get("min_grade_percent") is not None:
        grade = _profile_get(profile, "grade_percent")
        need = rules["min_grade_percent"]
        if grade is None:
            c.append(Criterion("grades", "unknown", f"Add your HSSC/A-Level percentage (needs about {need}%)."))
        elif float(grade) >= need:
            c.append(Criterion("grades", "pass", f"Your {grade}% meets the {need}% guideline."))
        else:
            c.append(Criterion("grades", "fail", f"Your {grade}% is below the {need}% guideline."))
    if level == "master" and rules.get("min_cgpa_percent") is not None:
        pct = cgpa_percent(profile)
        need = rules["min_cgpa_percent"]
        if pct is None:
            c.append(Criterion("CGPA", "unknown", f"Add your CGPA (needs about {need}% of the scale)."))
        elif pct >= need:
            c.append(Criterion("CGPA", "pass", f"Your CGPA is {pct}% of the scale, which meets the {need}% guideline."))
        else:
            c.append(Criterion("CGPA", "fail", f"Your CGPA is {pct}% of the scale, below the {need}% guideline."))

    # Work experience
    if rules.get("min_work_experience_months"):
        need = rules["min_work_experience_months"]
        have = _profile_get(profile, "work_experience_months")
        if have is None:
            c.append(Criterion("work experience", "unknown", f"Add your work experience (needs {need} months)."))
        elif int(have) >= need:
            c.append(Criterion("work experience", "pass", f"You have {have} months; {need} are required."))
        else:
            c.append(Criterion("work experience", "fail", f"You have {have} months of work experience; {need} are required ({need - int(have)} more).", fixable=True))

    # Degree age
    if rules.get("max_years_since_graduation"):
        limit = rules["max_years_since_graduation"]
        grad = _profile_get(profile, "graduation_year")
        if grad is None:
            c.append(Criterion("degree age", "unknown", f"Add your graduation year (degree must be under {limit} years old)."))
        elif today.year - int(grad) <= limit:
            c.append(Criterion("degree age", "pass", f"Your degree ({grad}) is within the {limit}-year limit."))
        else:
            c.append(Criterion("degree age", "fail", f"Your degree ({grad}) is older than the {limit}-year limit."))

    # English
    english = rules.get("english") or {}
    if english.get("required") and not english.get("accepted"):
        # Proof is needed, but each university or course sets the score.
        have = [f"{label} {_profile_get(profile, k)}" for k, label in ENGLISH_TESTS.items() if _profile_get(profile, k) is not None]
        detail = "Proof of English is required; the score is set by the university or course."
        if have:
            detail += f" Check that your {have[0]} meets your course's requirement."
        c.append(Criterion("English", "unknown", detail, fixable=not have))
        if english.get("note"):
            result.manual_checks.append(english["note"])
    elif english.get("required"):
        accepted: dict[str, float] = english.get("accepted", {})
        scores = {k: _profile_get(profile, k) for k in accepted}
        have = {k: v for k, v in scores.items() if v is not None}
        met = [k for k, v in have.items() if float(v) >= accepted[k]]
        options = " or ".join(f"{ENGLISH_TESTS[k]} {v}" for k, v in accepted.items())
        if met:
            k = met[0]
            c.append(Criterion("English", "pass", f"Your {ENGLISH_TESTS[k]} {have[k]} meets {accepted[k]}."))
        elif have:
            k, v = next(iter(have.items()))
            c.append(Criterion("English", "fail", f"Your {ENGLISH_TESTS[k]} {v} is below the requirement ({options}).", fixable=True))
        else:
            c.append(Criterion("English", "fail", f"You need an English test: {options}.", fixable=True))
        if english.get("note"):
            result.manual_checks.append(english["note"])
    elif english.get("note"):
        result.manual_checks.append(english["note"])

    # Financial need
    if rules.get("requires_financial_need"):
        need = _profile_get(profile, "financial_need")
        if need is None:
            c.append(Criterion("financial need", "unknown", "This award is need-based. Tell us whether your family can afford study abroad."))
        elif need:
            c.append(Criterion("financial need", "pass", "This is a need-based award and you indicated financial need."))
        else:
            c.append(Criterion("financial need", "fail", "This award is only for students who cannot afford study without it."))

    result.manual_checks.extend(rules.get("other", []))

    statuses = {x.status for x in c}
    if "fail" in statuses:
        result.verdict = "not_eligible"
    elif "unknown" in statuses:
        result.verdict = "likely_eligible"
    else:
        result.verdict = "eligible"
    return result


def rank_matches(results: list[EligibilityResult]) -> list[EligibilityResult]:
    order = {"eligible": 0, "likely_eligible": 1, "not_eligible": 2}

    def key(r: EligibilityResult) -> tuple[int, int]:
        fails = sum(1 for x in r.criteria if x.status == "fail")
        return order[r.verdict], fails

    return sorted(results, key=key)
