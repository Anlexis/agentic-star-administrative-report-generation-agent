"""AgentCore Platform v1.0"""

# State is a flat TypedDict — never a Pydantic model. Graph checkpoints are
# serialized with msgpack, which cannot round-trip arbitrary objects, so a
# model in a State field corrupts silently. Extend AgentState with
# agent-specific fields only, and never put credentials or secrets in one.
#
# ⚠️ Msgpack safety: structured fields (dict / list[dict]) are stored as JSON
# STRINGS, not bare Python containers. Producers serialize with to_json() on
# write; consumers deserialize with from_json() on read.
#
# GOV-C2-007 — Administrative Report Generator Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# Personal-data note (APPI / 個人情報保護法): resident personal data — the
# individual identification number, names, addresses, telephone numbers and
# e-mail — is rewritten out of caller text at the request boundary
# (src/services/caller_contract.py) before any field below is written.
# Downstream nodes never see raw resident identifiers, and the output gate
# refuses to release anything still matching those shapes.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input → the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for GOV-C2-007.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode / ReportGenerationGraphNode.merge_output
    # ------------------------------------------------------------------

    # Personal-data-stripped narrative produced by PreProcessNode. Raw input is
    # NOT persisted beyond PreProcessNode.
    validated_input: Optional[str]

    # JSON STRING (to_json) of the validated caller contract built at the
    # request boundary: report_type, subject, period, department, fields,
    # data_points, free_text, missing. Every value has already passed its
    # bounded, inert shape check. Written by PreProcessNode, carried across the
    # outer/inner boundary by the caller bridge, and re-seeded into inner state
    # by DomainWorkflowGraph._extra_initial_state().
    caller_contract: Optional[str]

    # JSON STRING (to_json) of the live report settings forwarded from
    # config/config.yaml by ReportGenerationGraphNode._parent_config().
    # Deserialised shape: {"report_template_path": str,
    #                      "section_templates": {report_type: [section, ...]}}
    report_config: Optional[str]

    # Final rendered administrative report (Markdown / structured text).
    # Written by ReportRenderingNode; surfaced via merge_output.
    administrative_report: Optional[str]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # IntakeValidationNode outputs
    # JSON STRING (to_json) of the parsed, APPI-sanitized case record; resident
    # PII already stripped.  Deserialised dict shape:
    # {"report_type": str, "subject": str, "period": str,
    #  "fields": {...}, "data_points": [...], ...}
    # Consumers (DataExtractionNode / SectionPlanningNode / ReportDraftingNode /
    # ReportRenderingNode) read it back via from_json().
    case_record: Optional[str]

    # JSON STRING (to_json) of the list of malformed / rejected / redacted
    # intake notes (no PII).  Deserialised shape: list[str].
    intake_notes: Optional[str]

    # DataExtractionNode outputs
    # JSON STRING (to_json) of structured facts pulled from the case record,
    # keyed by report section.  Deserialised dict shape:
    # {"background": [...], "findings": [...], "metrics": {...}, ...}
    # Consumers (SectionPlanningNode / ReportDraftingNode) read it via from_json().
    extracted_facts: Optional[str]

    # SectionPlanningNode outputs
    # JSON STRING (to_json) of the ordered list of report sections to render,
    # resolved against the ministry / agency template (header, background,
    # findings, recommendations, next_actions, ...).  Deserialised shape:
    # list[dict], each entry {"key": str, "title": str, "required": bool,
    #              "source_facts": [str], ...}
    # Consumers (ReportDraftingNode / ReportRenderingNode) read it via from_json().
    section_plan: Optional[str]

    # ReportDraftingNode output
    # JSON STRING (to_json) of the narrative draft, section-keyed. Raw
    # resident data is never reintroduced here.
    # Deserialised dict shape:
    # {"background": str, "findings": str, "recommendations": str,
    #  "next_actions": str, ...}
    # Consumer (ReportRenderingNode) reads it back via from_json().
    section_drafts: Optional[str]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    # node_history inherited from AgentState; listed here for clarity
    # node_history: Optional[List[str]]
