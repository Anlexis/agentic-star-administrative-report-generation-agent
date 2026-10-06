"""AgentCore Platform v1.0"""

# GOV-C2-007 — outer graph (two-layer nested Cat 2 architecture)
#
# AdministrativeReportGeneratorAgent — turns a government office's case,
# project or survey data into a finished administrative report.
#
# Architecture:
#
#   Outer backbone (fixed — do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                             |  (retry, bounded by max_retry)
#                                             -> pre_process
#
#   The `main` slot is a GraphNode subclass (ReportGenerationGraphNode) that
#   delegates the whole domain workflow to DomainWorkflowGraph (inner graph:
#   intake_validation -> data_extraction -> section_planning -> report_drafting
#   -> report_rendering).
#
#   Domain complexity is fully encapsulated inside the inner graph; the outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#   src/graph/context_bridge.py        <- validated caller contract across the boundary
#
# Class-name contract:
#   graph.py class:           GovC2007Agent (this file)
#   config/agent.yaml class:  "src.graph.graph.Graph"
#   src/api/server.py import: from src.graph.graph import GovC2007Agent
#
# Rules enforced:
#   - GovC2007Agent inherits AgentBaseGraph (direct framework inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - ReportGenerationGraphNode assigned to self._nodes["main"]
#   - _parent_config() forwards the LIVE runtime config (never {})
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No platform SDK imports

from pathlib import Path
from typing import Any, ClassVar, Dict, List

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_state import AgentState
from framework.utils.config_loader import load_agent_config
from src.graph.context_bridge import set_caller_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json
from src.services.caller_contract import (
    CallerDataError,
    parse_label,
    parse_render_text,
    parse_template_path,
)

# Repo root: src/graph/graph.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Mirrors config/config.yaml, so the report settings are never empty even where
# the config file is unreadable in an exotic deployment layout.
_FALLBACK_REPORT: Dict[str, Any] = {
    "report_template_path": "config/admin_report_template.md",
    "section_templates": {},
}

MAX_SECTIONS = 20
MAX_SECTION_TITLE_CHARS = 120


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml — the live runtime parameters.

    The registry loads this file and passes it to the graph constructor; the
    standalone HTTP entry point does the same, so `max_retry` and the report
    settings are live in both deployments rather than declared and ignored.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only,
    and a reader pointed at it degrades silently to defaults.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


def _validated_section_templates(raw: object) -> Dict[str, List[Dict[str, Any]]]:
    """Validate the configured per-report-type section templates.

    Section keys and titles are rendered as report headings, so they go through
    the same inert-label and bounded-text checks caller data does. An entry that
    fails any check is dropped rather than raised: a malformed configuration
    file must not stop the agent from compiling, and the built-in ministry
    section set is always a correct answer.
    """
    if not isinstance(raw, dict):
        return {}
    templates: Dict[str, List[Dict[str, Any]]] = {}
    for report_type, sections in raw.items():
        if not isinstance(sections, list) or not sections or len(sections) > MAX_SECTIONS:
            continue
        try:
            key = parse_label(report_type, field="report.section_templates")
            entries: List[Dict[str, Any]] = []
            for section in sections:
                if not isinstance(section, dict):
                    raise CallerDataError("section must be an object")
                section_key = parse_label(section.get("key"), field="report.section_templates.key")
                title = parse_render_text(
                    section.get("title") or section_key,
                    field="report.section_templates.title",
                    limit=MAX_SECTION_TITLE_CHARS,
                    required=True,
                )
                sources = section.get("source_facts")
                source_facts = [
                    parse_label(item, field="report.section_templates.source_facts")
                    for item in (sources if isinstance(sources, list) else [])
                ]
                entries.append(
                    {
                        "key": section_key,
                        "title": title,
                        "required": bool(section.get("required", True)),
                        "source_facts": source_facts,
                    }
                )
        except CallerDataError:
            continue
        templates[key] = entries
    return templates


class ReportGenerationGraphNode(GraphNode):
    """The `main` slot: wraps the inner administrative-report workflow.

    Contracts:
      get_subgraph()    - instantiate DomainWorkflowGraph with the forwarded
                          runtime config (_parent_config())
      extract_input()   - hand the validated narrative to the inner graph and
                          stash the validated caller contract on the bridge
      merge_output()    - map sub_result fields into the outer state delta
      error_strategy    - "propagate": re-raise inner errors (fail fast)
    """

    # S-1 declared on the wrapper too: the CI gate only AST-scans FunctionNode
    # subclasses, so a GraphNode main slot passes the pipeline without one and is
    # flagged at review. Same level the nodes in this repo already declare.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # "propagate": re-raise inner graph exceptions as SubgraphError (fail fast).
    # "handle": call on_subgraph_error() instead — for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: human-review interrupts are handled inside the inner graph only.
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the live report settings to the inner graph.

        Returns the settings under config["configurable"] — never an empty dict.
        The inner graph republishes them into inner state
        (DomainWorkflowGraph._extra_initial_state()) so the planning and
        rendering nodes read live values: node execute() methods take no config
        parameter, so state seeding is the only route config can travel.
        """
        report = runtime_config().get("report")
        if not isinstance(report, dict) or not report:
            report = dict(_FALLBACK_REPORT)
        try:
            template_path = parse_template_path(report.get("report_template_path"), field="report.report_template_path")
        except CallerDataError:
            template_path = ""
        settings: Dict[str, Any] = {
            "report_template_path": template_path or _FALLBACK_REPORT["report_template_path"],
            "section_templates": _validated_section_templates(report.get("section_templates")),
        }
        return {"configurable": {"report": settings}}

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        Imported inside the method to avoid circular-import risk at module load
        time. The inner graph receives the runtime-derived config through its
        constructor; its domain nodes still take no constructor arguments and
        read config per call from seeded state.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the narrative string, and bridge the validated caller contract.

        The framework hands only a string to the inner graph, so the structured
        part of the request travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Only the contract the pre_process node already validated crosses.
        """
        set_caller_contract(from_json(state.get("caller_contract"), {}) or {})
        return str(state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner result back into the outer state delta (changed keys only).

        Key coupling, designed together with DomainWorkflowGraph.get_output():

          Inner get_output() emits  -> "administrative_report", "status", ...
          This merge_output() reads -> the same two keys

        `result` is set as well as `administrative_report`: the post_process
        slot and the output gate both read state["result"], so without that
        mapping the gated output would always be empty.
        """
        return {
            "administrative_report": sub_result.get("administrative_report"),
            "result": sub_result.get("administrative_report"),
            "intake_notes": sub_result.get("intake_notes"),
            "section_plan": sub_result.get("section_plan"),
            "status": sub_result.get("status"),
        }


class GovC2007Agent(AgentBaseGraph):
    """Outer graph for GOV-C2-007.

    Inherits AgentBaseGraph directly. Domain logic is fully encapsulated in
    ReportGenerationGraphNode (main slot), which delegates to
    DomainWorkflowGraph.

    Backbone (fixed):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY topology override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode (trust gate + caller-contract validation)
      - main:         ReportGenerationGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output gate)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the agent registry."""
        return "AdministrativeReportGeneratorAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize node (schema version, session id, trust
        level) and finalize node (response metadata, total elapsed time).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = ReportGenerationGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Back-compat alias — config/agent.yaml names the class by dotted path.
Graph = GovC2007Agent
