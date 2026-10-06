# GOV-C2-007 — Unit Tests: nested Cat-2 graph composition (end-to-end)
#
# Drives the REAL outer agent (GovC2007Agent / Graph) end-to-end via the
# AgentBaseGraph compile-on-first-use invoke() path — the same pattern used by
# the reviewed two-layer reference shape. The inner DomainWorkflowGraph runs all 5
# domain nodes; ReportRenderingNode sets status=SUCCESS, which the outer
# merge_output maps to the outer state so the backbone routes
# main → post_process → finalize.
#
# Deterministic — the nodes synthesize the report rule-based (no LLM, no
# network). framework.* / src.* imports only.

import json


from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext

from src.graph.graph import GovC2007Agent, Graph
from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.schemas.state import State


_PAYLOAD = json.dumps(
    {
        "report_type": "survey",
        "subject": "Road maintenance survey FY2025",
        "period": "2025-04 .. 2025-09",
        "department": "Public Works Bureau",
        "fields": {
            "background": "Annual road-surface inspection across 12 districts.",
            "findings": "3 districts show pavement degradation beyond threshold.",
            "recommendation": "Prioritize resurfacing in the 3 flagged districts.",
        },
        "data_points": [{"district": "01", "score": 72}, {"district": "02", "score": 48}],
    },
    ensure_ascii=False,
)


class TestOuterGraphConstruction:
    def test_state_schema_is_state(self):
        assert GovC2007Agent().state_schema is State

    def test_compile_fills_all_backbone_slots(self):
        agent = GovC2007Agent()
        agent.compile()
        for slot in ("initialize", "pre_process", "main", "post_process", "finalize"):
            assert agent._nodes.get(slot) is not None, f"backbone slot not filled: {slot}"


class TestInnerGraphConstruction:
    def test_inner_graph_registers_five_domain_nodes(self):
        inner = DomainWorkflowGraph()
        inner.register_nodes()
        assert set(inner._nodes.keys()) == {
            "intake_validation",
            "data_extraction",
            "section_planning",
            "report_drafting",
            "report_rendering",
        }

    def test_inner_graph_name_and_schema(self):
        inner = DomainWorkflowGraph()
        assert inner.name == "gov_c2_007_admin_report_workflow"
        assert inner.state_schema is State

    def test_inner_graph_get_output_shape(self):
        inner = DomainWorkflowGraph()
        out = inner.get_output({"administrative_report": "R", "status": AgentStatus.SUCCESS.value})
        assert out["administrative_report"] == "R"
        assert out["status"] == AgentStatus.SUCCESS.value


class TestEndToEndInvoke:
    """Full agent run: outer backbone + inner domain workflow, no LLM."""

    def _run(self, user_input: str) -> dict:
        agent = Graph()  # back-compat alias for GovC2007Agent
        # Secure by default: the backbone trust gate denies ANONYMOUS callers
        # (PreProcessNode requires VERIFIED_EXTERNAL, the domain nodes INTERNAL).
        # Supply a fully-trusted internal context, exactly as AgentGateway would
        # for an authenticated internal caller.
        ctx = InvocationContext.for_internal(caller_id="test-suite")
        return agent.invoke(user_input, ctx=ctx)

    def test_invoke_returns_success(self):
        result = self._run(_PAYLOAD)
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected success, got {result.get('status')}. result={result!r}"

    def test_invoke_output_is_populated_report(self):
        result = self._run(_PAYLOAD)
        # Outer get_output() surfaces formatted_output (mapped from the inner
        # administrative_report) under "output".
        output = result.get("output")
        assert isinstance(output, str) and output.strip(), f"Expected a non-empty report in output, got {output!r}"

    def test_report_contains_subject_and_sections(self):
        result = self._run(_PAYLOAD)
        output = result.get("output", "")
        assert "Road maintenance survey FY2025" in output
        # The ministry template renders the default section titles.
        assert "Background" in output
        assert "Findings" in output

    def test_report_reflects_input_findings(self):
        result = self._run(_PAYLOAD)
        output = result.get("output", "")
        assert "pavement degradation" in output

    def test_node_history_records_backbone_traversal(self):
        result = self._run(_PAYLOAD)
        history = result.get("node_history", [])
        assert isinstance(history, list)
        # BaseNode.__call__ appends each node's CLASS name (not the slot name).
        # The outer backbone runs the request boundary (PreProcessNode), the
        # main slot wrapper (ReportGenerationGraphNode) and the output gate
        # (PostProcessNode).
        for cls_name in ("PreProcessNode", "ReportGenerationGraphNode", "PostProcessNode"):
            assert cls_name in history, f"node_history missing {cls_name}: {history}"

    def test_free_text_input_still_produces_report(self):
        """Non-JSON input is wrapped by IntakeValidation; pipeline still completes."""
        result = self._run("Summarize the quarterly bridge inspection programme.")
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert (result.get("output") or "").strip()
