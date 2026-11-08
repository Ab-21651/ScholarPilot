from datetime import date

from scholarpilot.catalog import load_seed_file
from scholarpilot.tools.eligibility import check_eligibility, next_deadline
from scholarpilot.tools.planner import build_timeline

CATALOG = {s["id"]: s for s in load_seed_file()}
TODAY = date(2026, 3, 1)


def statuses(result):
    return {c.name: c.status for c in result.criteria}


def entry(level="master", deadline=None, **rules):
    """A made-up scholarship for testing the rule engine on its own."""
    rules.setdefault("nationality", "any")
    return {
        "id": "test", "name": "Test Scholarship", "official_url": "https://example.org/",
        "apply_url": "https://example.org/apply",
        "tracks": {level: {"eligibility": rules, "deadline": deadline or {"month": 1, "day": 15, "approximate": True},
                           "documents": ["CV"], "steps": ["Apply"]}},
    }


IELTS_65 = {"required": True, "accepted": {"ielts": 6.5, "toefl_ibt": 90}}


def test_master_with_everything_is_eligible():
    profile = {"nationality": "Pakistan", "cgpa": 3.4, "cgpa_scale": 4, "ielts": 7.0}
    r = check_eligibility(entry(english=IELTS_65, min_cgpa_percent=70), profile, "master", TODAY)
    assert r.verdict == "eligible"
    assert statuses(r)["English"] == "pass"


def test_low_ielts_is_a_fixable_gap():
    r = check_eligibility(entry(english=IELTS_65), {"cgpa": 3.4, "ielts": 6.0}, "master", TODAY)
    assert r.verdict == "not_eligible"
    english = next(c for c in r.criteria if c.name == "English")
    assert english.status == "fail" and english.fixable
    assert "6.5" in english.detail


def test_english_set_by_university_is_unknown_not_fail():
    # Erasmus Mundus: each programme sets its own score.
    s = CATALOG["erasmus-mundus"]
    none = check_eligibility(s, {}, "master", TODAY)
    assert statuses(none)["English"] == "unknown" and none.verdict == "likely_eligible"
    has = check_eligibility(s, {"ielts": 7}, "master", TODAY)
    english = next(c for c in has.criteria if c.name == "English")
    assert english.status == "unknown" and "IELTS 7" in english.detail


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
    # 2027 guidelines: born on or after 2 April 2002, i.e. at most 24 on 1 April 2027.
    s = CATALOG["mext-undergraduate"]
    ok = {"date_of_birth": "2002-04-02", "grade_percent": 85}
    too_old = {"date_of_birth": "2002-04-01", "grade_percent": 85}
    assert statuses(check_eligibility(s, ok, "bachelor", TODAY))["age"] == "pass"
    r = check_eligibility(s, too_old, "bachelor", TODAY)
    assert statuses(r)["age"] == "fail"
    assert r.verdict == "not_eligible"


def test_mext_research_age_limit():
    # 2027 guidelines: born on or after 2 April 1992.
    s = CATALOG["mext-research"]
    assert statuses(check_eligibility(s, {"date_of_birth": "1992-04-02"}, "master", TODAY))["age"] == "pass"
    assert statuses(check_eligibility(s, {"date_of_birth": "1992-04-01"}, "master", TODAY))["age"] == "fail"


def test_jds_age_window_and_service():
    s = CATALOG["jds-pakistan"]
    today = date(2026, 11, 8)  # deadline 27 Nov 2026, age measured on 1 April 2027
    young = check_eligibility(s, {"date_of_birth": "2002-06-01", "work_experience_months": 40}, "master", today)
    assert statuses(young)["age"] == "fail"
    ok = check_eligibility(s, {"date_of_birth": "1990-01-01", "work_experience_months": 40}, "master", today)
    assert statuses(ok)["age"] == "pass" and statuses(ok)["work experience"] == "pass"


def test_cgpa_on_other_scales():
    s = CATALOG["cafoscari-honours"]  # CGPA at least 85%
    assert statuses(check_eligibility(s, {"cgpa": 3.2, "cgpa_scale": 4}, "master", TODAY))["CGPA"] == "fail"
    assert statuses(check_eligibility(s, {"cgpa": 4.5, "cgpa_scale": 5}, "master", TODAY))["CGPA"] == "pass"


def test_need_based_award():
    s = CATALOG["commonwealth-shared"]
    assert statuses(check_eligibility(s, {"financial_need": False}, "master", TODAY))["financial need"] == "fail"
    assert check_eligibility(s, {"financial_need": True}, "master", TODAY).verdict == "eligible"


def test_unconfirmed_nationality_is_unknown():
    r = check_eligibility(CATALOG["italy-maeci"], {"date_of_birth": "2000-01-01"}, "master", TODAY)
    assert statuses(r)["nationality"] == "unknown"
    assert r.verdict == "likely_eligible"


def test_next_deadline_rolls_over():
    assert next_deadline({"month": 1, "day": 15}, TODAY) == date(2027, 1, 15)
    assert next_deadline({"month": 10, "day": 7}, TODAY) == date(2026, 10, 7)
    assert next_deadline({"status": "varies", "note": "set by each course"}, TODAY) is None


def test_timeline_adds_english_steps_and_warnings():
    s = entry(english=IELTS_65, min_work_experience_months=24, deadline={"month": 10, "day": 7, "approximate": True})
    r = check_eligibility(s, {"work_experience_months": 30}, "master", TODAY)
    t = build_timeline(s, r, TODAY)
    titles = [m["title"] for m in t["milestones"]]
    assert t["deadline"] == "2026-10-07"
    assert titles[-1] == "Deadline"
    assert t["milestones"] == sorted(t["milestones"], key=lambda m: m["date"])
    assert any("approximate" in w for w in t["warnings"])
    assert any("English test" in m["title"] for m in t["milestones"])
    assert t["gaps"]

    # 14 months now + ~19 months until Oct 2027 is enough; 14 months with the Oct 2026 deadline is not.
    p = {"work_experience_months": 14, "ielts": 7}
    later = build_timeline(s, check_eligibility(s, p, "master", date(2026, 11, 7)), date(2026, 11, 7), profile=p)
    assert any("aim for this cycle" in w for w in later["warnings"])
    soon = build_timeline(s, check_eligibility(s, p, "master", TODAY), TODAY, profile=p)
    assert any("later cycle" in w for w in soon["warnings"])


def test_timeline_without_a_single_deadline():
    s = CATALOG["daad-epos"]
    t = build_timeline(s, check_eligibility(s, {}, "master", TODAY), TODAY)
    assert t["deadline"] is None and not t["milestones"]
    assert any("each university or course" in w for w in t["warnings"])
