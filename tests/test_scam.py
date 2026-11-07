from scholarpilot.tools.scam import check_message

OFFICIAL = {"chevening.org", "daad.de", "daad.pk", "stipendiumhungaricum.hu", "europa.eu"}


def codes(report):
    return {s.code for s in report.signals}


def test_classic_fee_scam_is_high_risk():
    msg = "Congratulations! You are selected for a fully funded scholarship in Germany. Pay Rs 5000 processing fee on JazzCash within 24 hours to confirm your seat."
    r = check_message(msg, OFFICIAL)
    assert r.risk_level == "high"
    assert {"payment_request", "urgency"} <= codes(r)


def test_lookalike_domain():
    r = check_message("Apply now at https://www.chevenning.org/apply", OFFICIAL)
    assert "lookalike_domain" in codes(r)
    assert r.risk_level == "high"


def test_brand_misuse_domain():
    r = check_message("Official portal: http://daad-scholarship-pakistan.xyz/form", OFFICIAL)
    assert "brand_misuse" in codes(r)
    assert r.risk_level in {"medium", "high"}


def test_otp_request_is_high():
    r = check_message("Embassy officer here, please send the OTP you received to verify your MEXT application", OFFICIAL)
    assert "credentials" in codes(r)
    assert r.risk_level == "high"


def test_official_link_is_low_risk():
    r = check_message("Chevening applications are open: https://www.chevening.org/apply/", OFFICIAL)
    assert r.risk_level == "low"
    assert r.trusted_links


def test_shortened_link_and_free_email():
    r = check_message("Scholarship office: contact hungaricum.office@gmail.com or open bit.ly/sh-form", OFFICIAL)
    assert {"free_email", "shortened_link"} <= codes(r)
    assert r.risk_level != "low"


def test_roman_urdu_scam():
    r = check_message("Aap select ho gaye hain! Fees jama karwayein aur foran rabta karein 0300 1234567 WhatsApp karein", OFFICIAL)
    assert "payment_request" in codes(r)
    assert r.risk_level == "high"


def test_plain_question_is_low():
    r = check_message("When does the Stipendium Hungaricum application usually open?", OFFICIAL)
    assert r.risk_level == "low"
    assert not r.signals
