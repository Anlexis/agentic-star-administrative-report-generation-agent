# PB — APPI Boundary: IntakeValidationNode strips resident PII from case_record
#
# GOV-C2-007 handles Japanese local-government data, so resident personal
# information (個人情報保護法 / APPI) must be redacted at intake BEFORE it is
# written to State. IntakeValidationNode performs the authoritative field-level
# redaction: PII-named keys (name/address/phone/email/my_number/...) have their
# values replaced with [REDACTED], and My Number / e-mail / phone PATTERNS in
# free-text values are pattern-redacted.
#
# This proof-of-boundary test asserts that, after IntakeValidationNode runs, the
# deserialized case_record contains NO raw resident-PII token.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from src.nodes.intake_validation_node import IntakeValidationNode
from src.schemas.state import from_json


# Simulated resident PII — NOT real data.
_MY_NUMBER = "9876-5432-1098"
_EMAIL = "resident.hanako@example.com"
_PHONE = "045-123-6789"
_NAME = "Suzuki Ichiro"
_ADDRESS = "4-5-6 Naka-ku, Yokohama"

_PAYLOAD = json.dumps(
    {
        "report_type": "incident",
        "subject": "Resident service request",
        "department": "Citizen Affairs",
        "fields": {
            "applicant_name": _NAME,
            "home_address": _ADDRESS,
            "phone_number": _PHONE,
            "email": _EMAIL,
            "my_number": _MY_NUMBER,
            # Free-text note carries only PATTERN PII (My Number / e-mail / phone),
            # which is pattern-redacted. The resident NAME is supplied only via the
            # PII-keyed applicant_name field (dropped by key match), since free-text
            # names are not pattern-detectable.
            "note": f"Case opened. My Number {_MY_NUMBER}, reachable at {_EMAIL} / {_PHONE}.",
        },
        "data_points": [{"case_age_days": 9}],
    },
    ensure_ascii=False,
)


def _case_record_after_intake() -> dict:
    result = IntakeValidationNode().execute({"validated_input": _PAYLOAD})
    return from_json(result["case_record"])


def _flatten(obj) -> str:
    """Serialize the whole case_record to one searchable string."""
    return json.dumps(obj, ensure_ascii=False)


class TestAppiResidentPiiStripped:
    def test_my_number_removed(self):
        blob = _flatten(_case_record_after_intake())
        assert _MY_NUMBER not in blob, "My Number survived APPI intake redaction"

    def test_email_removed(self):
        blob = _flatten(_case_record_after_intake())
        assert _EMAIL not in blob, "Resident e-mail survived APPI intake redaction"

    def test_phone_removed(self):
        blob = _flatten(_case_record_after_intake())
        assert _PHONE not in blob, "Resident phone survived APPI intake redaction"

    def test_pii_named_field_values_redacted(self):
        fields = _case_record_after_intake().get("fields", {})
        # PII-keyed fields have their value replaced with [REDACTED].
        assert fields.get("applicant_name") == "[REDACTED]"
        assert fields.get("home_address") == "[REDACTED]"
        assert fields.get("phone_number") == "[REDACTED]"
        assert fields.get("email") == "[REDACTED]"
        assert fields.get("my_number") == "[REDACTED]"

    def test_name_removed_everywhere(self):
        blob = _flatten(_case_record_after_intake())
        assert _NAME not in blob, "Resident name survived APPI intake redaction"

    def test_intake_notes_record_redaction(self):
        result = IntakeValidationNode().execute({"validated_input": _PAYLOAD})
        notes = from_json(result["intake_notes"])
        assert any("APPI" in str(n) for n in notes), "APPI redaction note missing"

    def test_non_pii_content_preserved(self):
        """Redaction must be targeted — non-PII fields survive intact."""
        case_record = _case_record_after_intake()
        assert case_record["report_type"] == "incident"
        assert case_record["subject"] == "Resident service request"
        assert case_record["department"] == "Citizen Affairs"
