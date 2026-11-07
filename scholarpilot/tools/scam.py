"""Scam checker for scholarship links and messages.

This is the rule-based version. A trained classifier will be added later and
combined with these rules. Every signal is explained, so the student sees *why*
something looks risky.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

URL_RE = re.compile(r"(https?://[^\s<>\"')]+|www\.[^\s<>\"')]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|info|xyz|top|click|online|site|pk|uk|de|hu|it|jp|au|eu|edu|gov|io|co|me|ly|gl|gy)(?:/[^\s<>\"')]*)?)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+", re.I)

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "rb.gy", "is.gd", "ow.ly", "shorturl.at", "tiny.cc", "rebrand.ly"}
RISKY_TLDS = {"xyz", "top", "click", "buzz", "online", "site", "info", "live", "icu", "loan", "work"}
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "aol.com", "proton.me", "protonmail.com"}
# Well-known official domains beyond the catalog
EXTRA_OFFICIAL = {"hec.gov.pk", "ibcc.edu.pk", "gov.uk", "britishcouncil.org", "studyinjapan.go.jp", "fulbright.org", "usefpakistan.org"}
BRAND_WORDS = {"chevening", "daad", "erasmus", "mundus", "mext", "hungaricum", "commonwealth", "fulbright", "australiaawards", "monbukagakusho", "deutschlandstipendium"}

PATTERNS: list[tuple[str, int, str, list[str]]] = [
    (
        "payment_request",
        35,
        "Asks you to pay. Real government scholarships never charge an application, processing or 'confirmation' fee to the scholarship itself.",
        [r"\b(processing|registration|application|confirmation|admission|visa|security|service)\s+(fee|charges?)\b", r"\bpay(ment)?\b.{0,40}\b(fee|rs\.?|pkr|usd|\$|dollars?)", r"\b(jazz\s?cash|easy\s?paisa|western union|moneygram|bank transfer|deposit)\b", r"\bfees?\s+(jama|bhej|transfer)", r"\brs\.?\s?\d{3,}"],
    ),
    (
        "guarantee",
        20,
        "Promises guaranteed admission, scholarship or visa. Nobody can guarantee these; selection is competitive.",
        [r"\bguarantee[ds]?\b", r"\b100\s?%\s+(visa|admission|success|scholarship)", r"\bconfirm(ed)?\s+(visa|admission|scholarship)\b"],
    ),
    (
        "urgency",
        15,
        "Pressures you to act immediately. Real calls publish fixed deadlines weeks in advance.",
        [r"\b(within|in)\s+\d+\s+(hours?|hrs?)\b", r"\b(today only|last chance|limited seats|hurry|act now|urgent(ly)?|immediately)\b", r"\b(foran|jaldi|abhi)\s+(apply|rabta|contact)"],
    ),
    (
        "sensitive_data",
        25,
        "Asks for sensitive personal or financial data. Share passport/CNIC copies only through the official portal, never over chat.",
        [r"\b(cnic|passport)\s+(copy|number|scan|pic|picture|photo)", r"\b(bank|account)\s+(details|number|statement)\b", r"\b(credit|debit)\s+card\b"],
    ),
    (
        "credentials",
        40,
        "Asks for a password or one-time code. No genuine office ever asks for these.",
        [r"\b(otp|one[- ]time (code|password)|verification code|password|pin code)\b"],
    ),
    (
        "too_good",
        15,
        "Offers that sound too good (no test, no requirements, free visa and tickets for everyone).",
        [r"\bno\s+(ielts|test|requirements?|interview)\b.{0,60}\b(fully|full|100%)\b", r"\bfree\s+(visa|air\s?ticket|flight)\b", r"\bwithout\s+ielts\b"],
    ),
    (
        "middleman",
        10,
        "Goes through a paid agent or consultant. Official scholarships are applied for directly, for free.",
        [r"\b(consultant|consultancy|our agent|visa agent|agency will|we will process)\b"],
    ),
    (
        "chat_only_contact",
        15,
        "Contact is only through WhatsApp/Telegram or a phone number, not an official office.",
        [r"\b(whatsapp|telegram)\s+(only|us|me|karein|kren|kar)\b", r"\b(whatsapp|contact|call)\b.{0,20}\+?92\s?3\d{2}[\s-]?\d{7}\b", r"\b03\d{2}[\s-]?\d{7}\b"],
    ),
]


@dataclass
class Signal:
    code: str
    weight: int
    explanation: str
    evidence: str


@dataclass
class ScamReport:
    risk_level: str  # low | medium | high
    score: int
    signals: list[Signal] = field(default_factory=list)
    trusted_links: list[str] = field(default_factory=list)
    urls_found: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _host(url: str) -> str:
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_official(host: str, official: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in official)


def _main_label(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "gov", "edu", "ac", "org", "go"}:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else host


def check_url(url: str, official: set[str]) -> tuple[list[Signal], bool]:
    signals: list[Signal] = []
    host = _host(url)
    if not host:
        return signals, False
    if _is_official(host, official):
        if url.lower().startswith("http://"):
            signals.append(Signal("no_https", 5, "Official domain, but the link is not HTTPS. Type the address yourself.", url))
        return signals, True

    try:
        ipaddress.ip_address(host)
        signals.append(Signal("ip_address", 30, "The link points to a raw IP address instead of a website name.", url))
    except ValueError:
        pass

    if host in SHORTENERS:
        signals.append(Signal("shortened_link", 20, "Shortened links hide where they really go.", url))

    tld = host.rsplit(".", 1)[-1]
    if tld in RISKY_TLDS:
        signals.append(Signal("risky_tld", 15, f"'.{tld}' domains are cheap and often used for scam sites.", url))

    label = _main_label(host)
    official_labels = {_main_label(d) for d in official}
    for ol in official_labels:
        if len(ol) >= 4 and label != ol and _levenshtein(label, ol) <= 2:
            signals.append(Signal("lookalike_domain", 40, f"'{host}' looks like the official '{ol}' domain but is different.", url))
            break
    else:
        if any(b in host.replace("-", "").replace(".", "") for b in BRAND_WORDS):
            signals.append(Signal("brand_misuse", 35, f"'{host}' uses a scholarship's name but is not its official website.", url))

    if url.lower().startswith("http://"):
        signals.append(Signal("no_https", 10, "The link is not secure (no HTTPS).", url))
    if not signals:
        signals.append(Signal("unverified_domain", 10, f"'{host}' is not one of the official domains we track. Find the call on the official site instead.", url))
    return signals, False


def check_message(text: str, official_domains: set[str] | None = None) -> ScamReport:
    official = set(official_domains or set()) | EXTRA_OFFICIAL
    lowered = text.lower()
    signals: list[Signal] = []

    for code, weight, explanation, patterns in PATTERNS:
        for p in patterns:
            m = re.search(p, lowered, re.I)
            if m:
                signals.append(Signal(code, weight, explanation, m.group(0)))
                break

    for m in EMAIL_RE.finditer(text):
        domain = m.group(0).split("@", 1)[1].lower()
        if domain in FREE_MAIL and re.search(r"\b(scholarship|embassy|admission|commission|official|ministry)\b", lowered):
            signals.append(Signal("free_email", 20, "An 'official' message sent from a free email address (Gmail/Yahoo). Offices use their own domain.", m.group(0)))
            break

    urls_found, trusted = [], []
    emails = {m.group(0) for m in EMAIL_RE.finditer(text)}
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        if any(url in e for e in emails):
            continue
        urls_found.append(url)
        url_signals, is_official = check_url(url, official)
        signals.extend(url_signals)
        if is_official:
            trusted.append(url)

    score = min(100, sum(s.weight for s in signals))
    if trusted and score > 0 and not any(s.code in {"payment_request", "credentials"} for s in signals):
        score = max(0, score - 10)

    strong = {"payment_request", "credentials", "lookalike_domain", "brand_misuse", "ip_address"}
    if score >= 50 or any(s.code in {"credentials", "lookalike_domain"} for s in signals):
        level = "high"
    elif score >= 25 or any(s.code in strong for s in signals):
        level = "medium"
    else:
        level = "low"

    advice = []
    if level != "low":
        advice.append("Do not pay any money or share documents through this link or chat.")
        advice.append("Find the same call on the scholarship's official website and apply only there.")
    if any(s.code == "credentials" for s in signals):
        advice.append("Never share a password or OTP. If you already did, change the password now.")
    if level == "low" and not trusted:
        advice.append("No obvious scam signs, but we couldn't confirm an official source. Check the official page before acting.")
    if level == "low" and trusted:
        advice.append("The link goes to an official domain. Still check the deadline and details on the page itself.")

    return ScamReport(risk_level=level, score=score, signals=signals, trusted_links=trusted, urls_found=urls_found, advice=advice)
