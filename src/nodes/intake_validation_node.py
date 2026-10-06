"""AgentCore Platform v1.0"""

# GOV-C2-007 — IntakeValidationNode
# Domain step 1: turn the validated caller contract into the case record the
# rest of the pipeline reads, and record what was redacted or missing.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).
#
# Where the data comes from: the request boundary (PreProcessNode) already
# screened, bounds-checked and personal-data-stripped everything the caller
# sent, and the caller bridge seeds that contract into inner state. This node
# therefore consumes the contract rather than re-parsing raw request text —
# there is one parser in the template, not two that could disagree.
#
# The direct-execution fallback below exists for callers that drive this node on
# its own (the boundary tests do): it runs the SAME contract builder over the
# narrative field, so a value that would be refused at the boundary is refused
# here too.

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.caller_contract import (
    REDACTION_STUB,
    CallerDataError,
    build_caller_contract,
)

logger = logging.getLogger(__name__)


class IntakeValidationNode(FunctionNode):
    """Build the case record from the validated caller contract.

    Input state keys:
        caller_contract: validated contract seeded by the caller bridge
        validated_input: narrative fallback for direct execution

    Output state keys (partial dict):
        case_record:  case record ready for extraction (no resident personal data)
        intake_notes: validation / redaction notes (no personal data)
    """

    # The manifest's declared caller level. The request boundary
    # (PreProcessNode) refuses anything below it before this node is
    # reachable, so this is defence in depth rather than the primary gate —
    # and declaring a HIGHER level here would refuse the declared caller and
    # make the public path unreachable.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        contract: Dict[str, Any] = from_json(state.get("caller_contract"), {}) or {}
        if not contract:
            raw = state.get("validated_input") or state.get("user_input", "")
            try:
                contract = build_caller_contract(raw, state.get("input_context", {}))
            except CallerDataError as rejected:
                emit_trace_event("case_record_rejected", {"reason": "caller_contract"}, state)
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [f"IntakeValidationNode: intake rejected — {rejected}"],
                    "case_record": to_json({}),
                    "intake_notes": to_json([f"Intake rejected — {rejected}"]),
                    # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                    # error_log reaches no one: the terminal result carries just `status`, and get_output()
                    # does not copy error_log out of the graph -- the caller sees a blank spinner.
                    "formatted_output": "Request could not be completed. "
                    + (f"IntakeValidationNode: intake rejected — {rejected}"),
                }

        intake_notes: List[str] = []
        missing = contract.get("missing") or []
        if missing:
            intake_notes.append(f"Missing recommended fields {sorted(missing)} — report sections may be incomplete.")

        case_record: Dict[str, Any] = {
            "report_type": contract.get("report_type", "general"),
            "subject": contract.get("subject", ""),
            "period": contract.get("period", ""),
            "department": contract.get("department", ""),
            "fields": contract.get("fields", {}),
            "data_points": contract.get("data_points", []),
            "free_text": contract.get("free_text", ""),
        }

        redacted_fields = sorted(key for key, value in case_record["fields"].items() if value == REDACTION_STUB)
        if redacted_fields:
            intake_notes.append(f"Fields named for resident personal data were withheld: {redacted_fields}.")
        intake_notes.append(
            "Resident personal data redacted at intake and refused at the output "
            "boundary, per APPI (個人情報保護法)."
        )

        emit_trace_event(
            "case_record_intaken",
            {
                "report_type": case_record["report_type"],
                "field_count": len(case_record["fields"]),
                "data_point_count": len(case_record["data_points"]),
                "withheld_field_count": len(redacted_fields),
            },
            state,
        )

        logger.info(
            "IntakeValidationNode: intaken report_type=%s, %d fields, %d records, %d notes",
            case_record["report_type"],
            len(case_record["fields"]),
            len(case_record["data_points"]),
            len(intake_notes),
        )

        return {
            "case_record": to_json(case_record),
            "intake_notes": to_json(intake_notes),
        }
