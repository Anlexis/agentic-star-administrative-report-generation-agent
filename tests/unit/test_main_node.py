# GOV-C2-007 — Unit Tests: main slot (ReportGenerationGraphNode)
#
# The `main` backbone slot of GovC2007Agent is a GraphNode subclass
# (ReportGenerationGraphNode) that delegates to the inner DomainWorkflowGraph.
# These tests exercise its three real contracts in isolation, without driving
# the whole agent (the full end-to-end run lives in test_graph_composition.py):
#
#   - get_subgraph()  -> returns a DomainWorkflowGraph instance (no ctor args)
#   - extract_input() -> reads validated_input (falls back to user_input)
#   - merge_output()  -> maps inner sub_result["administrative_report"] onto the
#                        outer state delta as administrative_report + result + status
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from framework.schemas.agent_status import AgentStatus

from src.graph.graph import ReportGenerationGraphNode, GovC2007Agent, Graph
from src.graph.domain_workflow_graph import DomainWorkflowGraph


class TestReportGenerationGraphNodeSubgraph:
    """get_subgraph() must return the inner DomainWorkflowGraph (no ctor args)."""

    def test_get_subgraph_returns_domain_workflow_graph(self):
        node = ReportGenerationGraphNode()
        sub = node.get_subgraph()
        assert isinstance(
            sub, DomainWorkflowGraph
        ), f"get_subgraph() must return DomainWorkflowGraph, got {type(sub).__name__}"

    def test_get_subgraph_returns_fresh_instances(self):
        """Each call constructs a new inner graph (no shared mutable state)."""
        node = ReportGenerationGraphNode()
        assert node.get_subgraph() is not node.get_subgraph()


class TestReportGenerationGraphNodeExtractInput:
    """extract_input() prefers validated_input, falls back to user_input."""

    def test_prefers_validated_input(self):
        node = ReportGenerationGraphNode()
        state = {"validated_input": "PII-stripped payload", "user_input": "raw"}
        assert node.extract_input(state) == "PII-stripped payload"

    def test_falls_back_to_user_input(self):
        node = ReportGenerationGraphNode()
        state = {"user_input": "raw officer request"}
        assert node.extract_input(state) == "raw officer request"

    def test_empty_when_neither_present(self):
        node = ReportGenerationGraphNode()
        assert node.extract_input({}) == ""


class TestReportGenerationGraphNodeMergeOutput:
    """merge_output() maps inner administrative_report -> outer delta keys."""

    def test_maps_administrative_report_to_outer_keys(self):
        node = ReportGenerationGraphNode()
        sub_result = {
            "administrative_report": "# Administrative Report\n\nbody...",
            "status": AgentStatus.SUCCESS.value,
        }
        delta = node.merge_output({}, sub_result)

        # The inner report must be surfaced under BOTH administrative_report
        # (the schema field) and result (what PostProcessNode reads — GOV-F1).
        assert delta["administrative_report"] == sub_result["administrative_report"]
        assert delta["result"] == sub_result["administrative_report"]
        assert delta["status"] == AgentStatus.SUCCESS.value

    def test_returns_only_changed_keys(self):
        """merge_output must return a small delta, not the full outer state."""
        node = ReportGenerationGraphNode()
        delta = node.merge_output(
            {"unrelated": "keep me out"},
            {"administrative_report": "r", "status": AgentStatus.SUCCESS.value},
        )
        assert set(delta.keys()) == {
            "administrative_report",
            "result",
            "intake_notes",
            "section_plan",
            "status",
        }

    def test_missing_report_yields_none(self):
        """If the inner graph emitted nothing, merge_output surfaces None (not KeyError)."""
        node = ReportGenerationGraphNode()
        delta = node.merge_output({}, {})
        assert delta["administrative_report"] is None
        assert delta["result"] is None
        assert delta["status"] is None


class TestOuterAgentIdentity:
    """GovC2007Agent identity + Graph alias + main-slot wiring."""

    def test_name_property(self):
        assert GovC2007Agent().name == "AdministrativeReportGeneratorAgent"

    def test_graph_alias_is_outer_class(self):
        assert Graph is GovC2007Agent

    def test_main_slot_is_report_generation_graph_node(self):
        agent = GovC2007Agent()
        agent.register_nodes()
        assert isinstance(agent._nodes["main"], ReportGenerationGraphNode)
