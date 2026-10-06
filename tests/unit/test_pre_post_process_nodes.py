# GOV-C2-007 — Unit Tests: PreProcessNode + PostProcessNode
#
# Outer backbone slots:
#   PreProcessNode  — caller-contract validation + personal-data strip →
#                     validated_input; rejects empty/non-string input.
#   PostProcessNode — reads state["result"] (mapped from the inner graph's
#                     administrative_report) and surfaces it as formatted_output.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import PreProcessNode
from src.nodes.post_process_node import PostProcessNode


class TestPreProcessSuccess:
    def test_valid_input_returns_validated_input(self):
        node = PreProcessNode()
        result = node.execute({"user_input": "Generate the FY2025 road maintenance report."})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] == "Generate the FY2025 road maintenance report."

    def test_enriched_context_carries_channel(self):
        node = PreProcessNode()
        result = node.execute({"user_input": "report please", "input_context": {"channel": "web"}})
        assert result["enriched_context"]["channel"] == "web"
        assert result["enriched_context"]["source"] == "AdministrativeReportGeneratorAgent"

    def test_surface_pii_pre_stripped(self):
        """My Number / e-mail / phone tokens are redacted before validated_input."""
        node = PreProcessNode()
        raw = "Applicant My Number 1234-5678-9012, email taro@example.com, tel 03-1234-5678."
        result = node.execute({"user_input": raw})
        vi = result["validated_input"]
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "1234-5678-9012" not in vi
        assert "taro@example.com" not in vi
        assert "03-1234-5678" not in vi
        assert "[REDACTED]" in vi


class TestPreProcessRejection:
    def test_empty_input_is_error(self):
        node = PreProcessNode()
        result = node.execute({"user_input": ""})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]

    def test_whitespace_only_is_error(self):
        node = PreProcessNode()
        result = node.execute({"user_input": "   \n\t "})
        assert result["status"] == AgentStatus.ERROR.value

    def test_missing_user_input_is_error(self):
        node = PreProcessNode()
        result = node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_non_string_input_is_error(self):
        node = PreProcessNode()
        result = node.execute({"user_input": {"malicious": "dict"}})
        assert result["status"] == AgentStatus.ERROR.value


class TestPostProcess:
    def test_surfaces_result_as_formatted_output(self):
        node = PostProcessNode()
        report = "# Administrative Report\n\nbody"
        result = node.execute({"result": report})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == report

    def test_empty_result_is_non_fatal(self):
        """Nothing was generated, so there is nothing to gate — this is the EMPTY
        path, not a blocked one. The blocked path asserts the fixed notice and the
        cleared fields (tests/unit/test_post_process_node.py); asserting a falsy
        value there would pass on a gate that withheld nothing."""
        node = PostProcessNode()
        result = node.execute({})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == ""
