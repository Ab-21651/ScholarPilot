from urllib.parse import urlparse

from scholarpilot.catalog import LEVELS, load_seed_file, search_scholarships

REQUIRED = {"id", "name", "country", "provider", "funding", "official_url", "apply_url", "official_domains", "monitor_urls", "tracks"}


def test_seed_entries_are_well_formed():
    entries = load_seed_file()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for e in entries:
        assert REQUIRED <= e.keys(), e["id"]
        assert e["funding"] in {"full", "partial"}
        assert set(e["tracks"]) <= set(LEVELS) and e["tracks"], e["id"]
        for url in [e["official_url"], e["apply_url"], *e["monitor_urls"]]:
            host = urlparse(url).hostname
            assert url.startswith("https://"), url
            assert any(host == d or host.endswith("." + d) for d in e["official_domains"]), f"{url} not on an official domain"
        for level, track in e["tracks"].items():
            assert {"eligibility", "deadline", "documents", "steps"} <= track.keys(), (e["id"], level)
            dl = track["deadline"]
            assert 1 <= dl["month"] <= 12 and 1 <= dl["day"] <= 31


def test_covers_agreed_countries():
    countries = {e["country"] for e in load_seed_file()}
    for c in ["Germany", "United Kingdom", "Hungary", "Italy", "Japan", "Australia"]:
        assert c in countries


def test_search_filters(db):
    bachelor = search_scholarships(db, level="bachelor")
    assert bachelor and all("bachelor" in s["tracks"] for s in bachelor)
    japan = search_scholarships(db, level="master", country="japan")
    assert {s["id"] for s in japan} == {"mext-research", "adb-jsp"}
    full = search_scholarships(db, level="master", funding="partial")
    assert all(s["funding"] == "partial" for s in full)
