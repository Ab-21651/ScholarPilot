from datetime import date

from scholarpilot.catalog import load_seed_file
from scholarpilot.tools.eligibility import check_eligibility, next_deadline
from scholarpilot.tools.planner import build_timeline

CATALOG = {s["id"]: s for s in load_seed_file()}
TODAY = date(2026, 3, 1)


def statuses(result):
    return {c.name: c.status for c in result.criteria}


def test_master_with_everything_is_eligible_for_erasmus():
    profile = {"nationality": "Pakistan", "cgpa": 3.4, "cgpa_scale": 4, "ielts": 7.0}
    r = check_eligibility(CATALOG["erasmus-mundus"], profile, "master", TODAY)
    assert r.verdict == "eligible"
    assert statuses(r)["English"] == "pass"


def test_low_ielts_is_a_fixable_gap():
    profile = {"cgpa": 3.4, "ielts": 6.0}
    r = check_eligibility(CATALOG["erasmus-mundus"], profile, "master", TODAY)
    assert r.verdict == "not_eligible"
    english = next(c for c in r.criteria if c.name == "English")
    assert english.status == "fail" and english.fixable
    assert "6.5" in english.detail


def test_chevening_needs_work_experience():
    r = check_eligibility(CATALOG["chevening"], {"work_experience_months": 10}, "master", TODAY)
    assert r.verdict == "not_eligible"
    assert "14 more" in next(c.detail for c in r.criteria if c.name == "work experience")


def test_missing_data_gives_likely_eligible():
    r = check_eligibility(CATALOG["chevening"], {}, "master", TODAY)
    assert r.verdict == "likely_eligible"
    assert statuses(r)["work experience"] == "unknown"


def test_wrong_level_is_not_eligible():
    r = check_eligibility(CATALOG["chevening"], {}, "bachelor", TODAY)
    assert r.verdict == "not_eligible"
    assert r.criteria[0].name == "study level"


def test_mext_undergraduate_age_limit():
    # Deadline 15 May 2026, so age is measured on 1 April 2027.
    young = {"date_of_birth": "2007-06-01", "grade_percent": 85}
    old = {"date_of_birth": "2001-01-01", "grade_percent": 85}
    assert statuses(check_eligibility(CATALOG["mext-undergraduate"], young, "bachelor", TODAY))["age"] == "pass"
    r = check_eligibility(CATALOG["mext-undergraduate"], old, "bachelor", TODAY)
    assert statuses(r)["age"] == "fail"
    assert r.verdict == "not_eligible"


def test_cgpa_on_other_scales():
    s = CATALOG["commonwealth-masters"]
    assert statuses(check_eligibility(s, {"cgpa": 2.4, "cgpa_scale": 4}, "master", TODAY))["CGPA"] == "fail"
    assert statuses(check_eligibility(s, {"cgpa": 3.5, "cgpa_scale": 5}, "master", TODAY))["CGPA"] == "pass"


def test_need_based_award():
    s = CATALOG["italy-dsu"]
    assert statuses(check_eligibility(s, {"financial_need": False, "ielts": 6}, "bachelor", TODAY))["financial need"] == "fail"
    assert check_eligibility(s, {"financial_need": True, "ielts": 6}, "bachelor", TODAY).verdict == "eligible"


def test_unconfirmed_nationality_is_unknown():
    r = check_eligibility(CATALOG["australia-awards"], {"work_experience_months": 30, "ielts": 7}, "master", TODAY)
    assert statuses(r)["nationality"] == "unknown"
    assert r.verdict == "likely_eligible"


def test_next_deadline_rolls_over():
    assert next_deadline({"month": 1, "day": 15}, TODAY) == date(2027, 1, 15)
    assert next_deadline({"month": 10, "day": 7}, TODAY) == date(2026, 10, 7)


def test_timeline_adds_english_steps_and_warnings():
    s = CATALOG["chevening"]
    r = check_eligibility(s, {"work_experience_months": 30}, "master", TODAY)
    t = build_timeline(s, r, TODAY)
    titles = [m["title"] for m in t["milestones"]]
    assert t["deadline"] == "2026-10-07"
    assert titles[-1] == "Deadline"
    assert t["milestones"] == sorted(t["milestones"], key=lambda m: m["date"])
    assert any("approximate" in w for w in t["warnings"])

    # 14 months now + ~19 months until Oct 2027 is enough; 14 months with the Oct 2026 deadline is not.
    p = {"work_experience_months": 14}
    later = build_timeline(s, check_eligibility(s, p, "master", date(2026, 11, 7)), date(2026, 11, 7), profile=p)
    assert any("aim for this cycle" in w for w in later["warnings"])
    soon = build_timeline(s, check_eligibility(s, p, "master", TODAY), TODAY, profile=p)
    assert any("later cycle" in w for w in soon["warnings"])

    erasmus = CATALOG["erasmus-mundus"]
    t2 = build_timeline(erasmus, check_eligibility(erasmus, {"ielts": 5.5}, "master", TODAY), TODAY)
    assert any("English test" in m["title"] for m in t2["milestones"])
    assert t2["gaps"]
