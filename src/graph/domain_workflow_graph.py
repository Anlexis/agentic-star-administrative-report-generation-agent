"""AgentCore Platform v1.0"""

# GOV-C2-007 — DomainWorkflowGraph (inner graph)
#
# The inner half of the two-layer nested Cat 2 architecture. It encapsulates the
# whole administrative-report workflow:
#
#   START -> intake_validation -> data_extraction -> section_planning
#         -> report_drafting -> report_rendering -> END
#
# Called by ReportGenerationGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - inherits BaseGraph (fully custom topology — no forced backbone)
#   - implements every BaseGraph abstract method
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - register_nodes() instantiates every domain node with NO ctor args
#   - does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with ReportGenerationGraphNode.merge_output()
#   - no platform SDK imports

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_caller_contract
from src.nodes.data_extraction_node import DataExtractionNode
from src.nodes.intake_validation_node import IntakeValidationNode
from src.nodes.report_drafting_node import ReportDraftingNode
from src.nodes.report_rendering_node import ReportRenderingNode
from src.nodes.section_planning_node import SectionPlanningNode
from src.schemas.state import State, to_json


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for GOV-C2-007.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by ReportGenerationGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> intake_validation  (IntakeValidationNode)   — contract intake + personal-data strip
          -> data_extraction    (DataExtractionNode)     — structure facts by section
          -> section_planning   (SectionPlanningNode)    — resolve the section template
          -> report_drafting    (ReportDraftingNode)     — narrative draft per section
          -> report_rendering   (ReportRenderingNode)    — render the final report
          -> END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "gov_c2_007_admin_report_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        The forwarded report settings are already shape-checked by
        ReportGenerationGraphNode._parent_config(), which drops anything
        malformed rather than passing it on, so there is nothing left to reject
        here. An absent block is not an error either: every setting has a
        built-in default and the report renders without any of them.
        """

    # ── Initial state ─────────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the inner state with everything that cannot travel as a string.

        The framework passes only the narrative string into a nested graph, so
        two things are seeded here instead:

        `report_config` — the live settings forwarded by
        ReportGenerationGraphNode._parent_config(). Domain nodes take no config
        parameter, so state seeding is the only route runtime config can reach
        SectionPlanningNode and ReportRenderingNode.

        `caller_contract` — the validated case contract stashed on the bridge by
        ReportGenerationGraphNode.extract_input() one step earlier. It carries
        the report type, the officer's section material and the measurement
        records, already bounds-checked by the request boundary.

        Both are stored as JSON strings rather than mappings, matching the
        serialization rule the shared state schema documents.
        """
        report = (self.config or {}).get("configurable", {}).get("report") or {}
        return {
            "report_config": to_json(report),
            "caller_contract": to_json(get_caller_contract()),
        }

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.

        Every node is instantiated with NO constructor arguments — a domain node
        implements execute(self, state) -> dict and reads its settings from
        seeded state, never from a constructor argument or a per-call config
        parameter. Every key registered here is referenced in add_edges().
        """
        self._nodes["intake_validation"] = IntakeValidationNode()
        self._nodes["data_extraction"] = DataExtractionNode()
        self._nodes["section_planning"] = SectionPlanningNode()
        self._nodes["report_drafting"] = ReportDraftingNode()
        self._nodes["report_rendering"] = ReportRenderingNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear administrative-report topology.

        Each step passes its partial-dict output into the shared State. The
        topology is intentionally linear — no conditional branching between
        domain nodes — so add_conditional_edges() is not used and route() is
        never reached at run time.
        """
        self._sg.add_edge(START, "intake_validation")
        self._sg.add_edge("intake_validation", "data_extraction")
        self._sg.add_edge("data_extraction", "section_planning")
        self._sg.add_edge("section_planning", "report_drafting")
        self._sg.add_edge("report_drafting", "report_rendering")
        self._sg.add_edge("report_rendering", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Conditional routing — required by the BaseGraph contract.

        Annotated with this graph's OWN State rather than the framework base
        state: a routing callable's annotation is read as its input schema and
        fields outside it are projected away, so a base-state annotation would
        hide every domain field from the decision. The topology is linear and
        add_conditional_edges() is not used, so this is never called at run
        time; it returns END on error so an unexpected call cannot re-enter a
        processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "report_rendering"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by ReportGenerationGraphNode.merge_output() in
        graph.py as the `sub_result` argument. Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output()  emits: "administrative_report", "status", ...
            Outer merge_output() reads: the same keys

        section_plan and intake_notes travel as the JSON strings they are stored
        as; they are operational fields the deployment monitors, and the outer
        output gate scans them with the report before anything is released.
        """
        return {
            "administrative_report": state.get("administrative_report"),
            "status": state.get("status"),
            "section_plan": state.get("section_plan"),
            "intake_notes": state.get("intake_notes"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
