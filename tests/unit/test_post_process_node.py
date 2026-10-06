# GOV-C2-007 — Unit Tests: the output gate and its containment
#
# The gate's job is not only to notice a violation; it is to make sure nothing
# ungated leaves. The framework's output envelope falls back to state["result"]
# whatever the status, so a gate that merely returned an error — or that raised —
# would still ship the ungated report inside the error envelope. These tests pin
# the clearing, not just the status.
#
# Each guard was measured against a mutant rather than assumed, and the results
# are recorded here because they say which test proves which guard:
#
#   clearing removed            -> 1 failure: test_every_output_bearing_field_is_cleared
#   fixed notice made falsy     -> 3 failures, including the full-invoke
#                                  containment test in tests/proof_of_boundary/
#   ORIGINAL shipped branch     -> 1 failure: test_every_output_bearing_field_is_cleared
#   framework detector replaced -> 3 failures, including
#     by the original narrow        test_the_gate_is_never_narrower_than_the_framework_detector
#     local pattern set
#
# Two of those are worth stating plainly rather than leaving implied. The
# clearing of administrative_report / section_plan / intake_notes is falsifiable
# only at this level: the released envelope reads formatted_output or result, and
# the original branch already overwrote both, so a full-invoke test cannot see the
# difference. The clearing protects the checkpointed state and anything reading
# those fields for monitoring — a narrower claim than "it contains the envelope",
# and the accurate one. No extra layer is added elsewhere that would contain a
# leak on its own and make these unfalsifiable.
#
# Deterministic — no model, no network. framework.* / src.* imports only.

import pytest

from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import _OUTPUT_BEARING_FIELDS, PostProcessNode, security_gate_output
from src.schemas.state import to_json


# Simulated credentials — NOT real.
_STRIPE_KEY = "sk_live_" + "a" * 20
_OPENAI_KEY = "sk-" + "b" * 24
_AWS_KEY = "AKIA" + "C" * 16
# No inline user:password — the shape the framework detector matches is the
# scheme plus a host, and writing one with credentials in it would be a
# literal credential committed to the repository.
_CONN_STRING = "postgresql://reporting-db.example.com:5432/reports"
_JWT = "eyJ" + "d" * 14 + "." + "e" * 14 + "." + "f" * 14
_BEARER = "Bearer " + "g" * 24
_ASSIGNMENT = "password=super_secret_password_abc123"
_PUBLISHABLE_KEY = "pk-" + "h" * 20

_CLEAN_REPORT = (
    "# Administrative Report — Road Maintenance FY2025\n\n"
    "**Report Type:** general\n\n---\n\n"
    "## Findings\n\nNo major incidents. Two sections require resurfacing in Q3.\n"
)


def _state(result: str) -> dict:
    return {
        "result": result,
        "administrative_report": result,
        "section_plan": to_json([{"key": "findings", "title": "Findings"}]),
        "intake_notes": to_json(["Resident personal data redacted at intake."]),
    }


class TestGateRecognisers:
    @pytest.mark.parametrize(
        "secret",
        [_STRIPE_KEY, _OPENAI_KEY, _AWS_KEY, _CONN_STRING, _JWT, _BEARER, _ASSIGNMENT, _PUBLISHABLE_KEY],
    )
    def test_credential_shapes_are_named(self, secret):
        assert security_gate_output(f"Internal note: {secret}") is not None

    @pytest.mark.parametrize(
        "value",
        ["My Number 1234-5678-9012", "hanako@example.com", "03-9876-5432"],
    )
    def test_personal_data_shapes_are_named(self, value):
        assert security_gate_output(f"Contact: {value}") is not None

    def test_the_scan_walks_nested_structures(self):
        """Caller text can ride inside a nested value; a top-level-only scan
        reports zero findings on a payload whose leak sits one level down."""
        nested = {"sections": [{"body": f"token {_JWT}"}]}
        assert security_gate_output(nested) is not None

    def test_the_control_case_proves_the_scan_is_not_simply_always_positive(self):
        assert security_gate_output({"sections": [{"body": "No major incidents."}]}) is None

    def test_a_clean_report_is_not_flagged(self):
        assert security_gate_output(_CLEAN_REPORT) is None

    def test_the_finding_names_the_pattern_not_the_matched_text(self):
        name = security_gate_output(f"note {_OPENAI_KEY}")
        assert name is not None
        assert _OPENAI_KEY not in name

    def test_the_gate_is_never_narrower_than_the_framework_detector(self):
        """A gate narrower than the framework's anywhere is a containment bypass:
        the framework raises inside post-process on a value this node missed, and
        the wrapper then returns a bare error result that discards the clearing."""
        from framework.security.credential_detector import detect_credentials

        for secret in (_STRIPE_KEY, _OPENAI_KEY, _AWS_KEY, _CONN_STRING, _JWT, _BEARER):
            probe = f"report body {secret} end"
            assert detect_credentials(probe), "probe is not a framework finding — fix the probe"
            assert security_gate_output(probe) is not None


class TestBlockedOutputIsContained:
    def _blocked(self, secret: str = _OPENAI_KEY) -> dict:
        return PostProcessNode().execute(_state(f"# Report\n\nInternal note: {secret}\n"))

    def test_status_is_error(self):
        assert self._blocked()["status"] == AgentStatus.ERROR.value

    def test_every_output_bearing_field_is_cleared(self):
        blocked = self._blocked()
        for field in _OUTPUT_BEARING_FIELDS:
            assert field in blocked, f"{field} must be present in the delta so it overwrites state"
        assert blocked["administrative_report"] is None
        assert blocked["section_plan"] is None
        assert blocked["intake_notes"] is None

    def test_the_error_envelope_carries_no_released_text(self):
        blocked = self._blocked()
        joined = " ".join(str(v) for v in blocked.values())
        assert _OPENAI_KEY not in joined
        assert "Two sections require resurfacing" not in joined
        assert "Traceback" not in joined
        assert "src/nodes" not in joined

    def test_the_surfaced_fields_carry_the_fixed_notice(self):
        blocked = self._blocked()
        for field in ("formatted_output", "result"):
            assert "blocked" in str(blocked[field]).lower()

    def test_the_error_log_names_the_pattern_and_not_the_value(self):
        blocked = self._blocked()
        assert blocked["error_log"]
        assert _OPENAI_KEY not in " ".join(blocked["error_log"])

    def test_a_leak_hiding_in_an_operational_field_is_also_blocked(self):
        """The operational fields are released with the report, so they are gated
        with it — a scan of the report alone would pass this run."""
        state = _state(_CLEAN_REPORT)
        state["intake_notes"] = to_json([f"observed {_JWT}"])
        result = PostProcessNode().execute(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert _JWT not in " ".join(str(v) for v in result.values())


class TestCleanOutputPassesThrough:
    def test_a_clean_report_succeeds_unchanged(self):
        result = PostProcessNode().execute(_state(_CLEAN_REPORT))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == _CLEAN_REPORT

    def test_a_clean_run_does_not_clear_the_operational_fields(self):
        result = PostProcessNode().execute(_state(_CLEAN_REPORT))
        assert set(result.keys()) == {"formatted_output", "status"}

    def test_an_empty_result_is_forwarded_rather_than_blocked(self):
        result = PostProcessNode().execute({"result": ""})
        assert result["status"] == AgentStatus.SUCCESS.value
