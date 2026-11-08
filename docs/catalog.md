# Scholarship catalog: how it was built and checked

The catalog (`data/scholarships.json`) is the only place the agent gets scholarship facts from.
Every entry was checked against official pages on **2026-11-08**.

## Coverage

| Country | Scholarships | Bachelor's | Master's |
|---|---|---|---|
| Germany | DAAD EPOS, DAAD Helmut-Schmidt, Deutschlandstipendium | 1 | 3 |
| United Kingdom | Chevening, Commonwealth Master's, Commonwealth Shared, GREAT, Rhodes (Pakistan) | 0 | 5 |
| Hungary | Stipendium Hungaricum | 1 | 1 |
| Italy | MAECI, regional DSU, Padua Excellence, Politecnico di Milano Merit, Ca' Foscari Honours | 3 | 5 |
| Europe | Erasmus Mundus Joint Master's | 0 | 1 |
| Japan | MEXT Undergraduate, MEXT Research, ADB-Japan, JDS (government officers) | 1 | 3 |
| Australia | Australia Awards, Monash International Merit | 1 | 1 |

That makes 21 scholarships: 7 with a bachelor's track and 19 with a master's track.

## Rules we followed

1. **Official sources only:** the scholarship's own site, the government or embassy, or the university.
   Each entry lists the pages and PDFs read in `sources`. News sites and scholarship blogs were not
   used. Several of them still show old figures (for example a EUR 900/month MAECI stipend; the
   2026/27 call says EUR 10,800 for 9 months).
2. **Pakistan must be confirmed.** Each entry was kept only if an official page names Pakistan or is
   open to all nationalities. Where the list changes yearly (MAECI, GREAT), `nationality_status` is
   `verify`, and the rule engine reports nationality as "unknown" rather than "pass".
3. **No invented numbers.** If an official page doesn't state an English score, age limit or grade,
   the entry doesn't either. For English, an empty `accepted` list means proof is needed but the
   university sets the score. The engine then says "unknown" instead of failing the student.
4. **Deadlines are honest.** `status` says whether the cycle is open, upcoming or closed as of the check
   date. `approximate: true` means the date is last cycle's because the new call isn't out. Scholarships
   where each course sets its own date have no month/day, and the planner says so instead of making up a
   timeline.

## What checking changed (compared with the first draft)

- **Removed:** Destination Australia. The government stopped new rounds on 1 July 2024.
- **Corrected:**
  - Deutschlandstipendium is EUR 300/month; there is no grade cut-off.
  - DAAD scholarships are EUR 992/month.
  - Stipendium Hungaricum needs a HAT/USAT score of at least 50% (HEC), and both portals.
  - MEXT 2027 age cut-offs (born on or after 2 April 2002 for undergraduates, 2 April 1992 for research
    students), stipends and embassy deadlines (26 May / 5 June 2026).
  - Commonwealth stipends are GBP 1,712/month (GBP 2,000 in London), and no IELTS is required by the CSC.
  - Italian Government (MAECI) master's applicants must be at most 28.
- **Removed claims we could not confirm:**
  - fixed IELTS scores for Erasmus Mundus, Stipendium Hungaricum and Australia Awards (each university
    or programme sets them)
  - a minimum CGPA for the Commonwealth Master's (the rule is a 2:1 degree, which is not a percentage)
  - "two years' work experience" for Australia Awards (not on the Pakistan pages read)
- **Added:**
  - Commonwealth Shared (open 10 Nov - 8 Dec 2026)
  - JDS Pakistan (closes 27 Nov 2026)
  - Rhodes Pakistan
  - Politecnico di Milano Merit (Early bird window ends 1 Dec 2026)
  - Padua Excellence, Ca' Foscari Honours and Monash International Merit

## Sites that block automated reading

GREAT (British Council), HEC, Destination Australia, the Embassy of Japan, the Australia Awards
handbook, MAECI (esteri.it) and Monash refused automated fetches, so they were read in a real browser.
The daily monitor only watches pages that allow automated reading.

## Keeping it current

- `seed_catalog()` loads the file at start-up. It refreshes a database row when the file's `checked` date
  is newer, so re-checked data reaches existing databases. Retired scholarships (`_meta.retired`) are
  hidden from search.
- The daily monitor flags changes on `monitor_urls` for a reviewer in the Review tab. An approval sets
  a newer `last_verified`, which the seed file will not overwrite.
- Re-check each entry when its new call is published, especially MAECI, Stipendium Hungaricum,
  Chevening, MEXT and the university awards for 2027/28.
