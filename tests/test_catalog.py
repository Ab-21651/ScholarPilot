from datetime import date
from urllib.parse import urlparse

from scholarpilot.catalog import LEVELS, get_scholarship, load_seed_file, retired_entries, search_scholarships, seed_catalog
from scholarpilot.models import Scholarship

REQUIRED = {"id", "name", "country", "provider", "funding", "funding_details", "official_url", "apply_url",
            "official_domains", "monitor_urls", "tracks", "checked", "sources"}
DEADLINE_STATUSES = {"open", "upcoming", "closed", "varies"}


def _on_official_domain(url, domains):
    host = urlparse(url).hostname
    return any(host == d or host.endswith("." + d) for d in domains)


def test_seed_entries_are_well_formed():
    entries = load_seed_file()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for e in entries:
        assert REQUIRED <= e.keys(), (e["id"], REQUIRED - e.keys())
        assert e["funding"] in {"full", "partial"}
        assert set(e["tracks"]) <= set(LEVELS) and e["tracks"], e["id"]
        date.fromisoformat(e["checked"])
        assert e["sources"], e["id"]
        for url in [e["official_url"], e["apply_url"], *e["monitor_urls"]]:
            assert url.startswith("https://"), url
            assert _on_official_domain(url, e["official_domains"]), f"{url} not on an official domain"
        for src in e["sources"]:
            if src.startswith("http"):
                assert _on_official_domain(src.split()[0], e["official_domains"]), f"{e['id']}: {src}"
        for level, track in e["tracks"].items():
            assert {"eligibility", "deadline", "documents", "steps"} <= track.keys(), (e["id"], level)
            dl = track["deadline"]
            assert dl.get("status") in DEADLINE_STATUSES, (e["id"], level)
            assert dl.get("note"), (e["id"], level)
            if "month" in dl:
                assert 1 <= dl["month"] <= 12 and 1 <= dl["day"] <= 31
            else:
                assert dl["status"] == "varies", (e["id"], level)


def test_covers_agreed_countries_and_both_levels():
    entries = load_seed_file()
    countries = {e["country"] for e in entries}
    for c in ["Germany", "United Kingdom", "Hungary", "Italy", "Japan", "Australia", "Europe (multiple countries)"]:
        assert c in countries
    assert sum("bachelor" in e["tracks"] for e in entries) >= 6
    assert len(entries) >= 20


def test_retired_entries_are_not_in_the_live_list():
    live = {e["id"] for e in load_seed_file()}
    for old in retired_entries():
        assert old["id"] not in live and old["reason"] and old["source"].startswith("https://")


def test_seeded_entries_are_verified(db):
    s = get_scholarship(db, "chevening")
    assert s["verification_status"] == "verified" and s["last_verified"] == s["checked"]


def test_seed_refreshes_old_rows_and_retires_removed_ones(db):
    # A database created before the re-check still has the old, unverified data.
    row = db.get(Scholarship, "commonwealth-masters")
    row.data, row.verification_status, row.last_verified = {**row.data, "funding_details": "old"}, "needs_review", None
    db.add(Scholarship(id="destination-australia", data={"id": "destination-australia", "name": "Destination Australia",
                                                        "country": "Australia", "tracks": {}}, verification_status="needs_review"))
    db.flush()
    seed_catalog(db)
    db.flush()
    fresh = get_scholarship(db, "commonwealth-masters")
    assert fresh["funding_details"] != "old" and fresh["verification_status"] == "verified"
    assert "destination-australia" not in {s["id"] for s in search_scholarships(db)}
    assert db.get(Scholarship, "destination-australia").verification_status == "retired"
    db.rollback()


def test_search_filters(db):
    bachelor = search_scholarships(db, level="bachelor")
    assert bachelor and all("bachelor" in s["tracks"] for s in bachelor)
    japan = search_scholarships(db, level="master", country="japan")
    assert {s["id"] for s in japan} == {"mext-research", "adb-jsp", "jds-pakistan"}
    partial = search_scholarships(db, level="master", funding="partial")
    assert partial and all(s["funding"] == "partial" for s in partial)
