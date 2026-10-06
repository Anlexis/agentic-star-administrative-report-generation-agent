# Template Design Specification — GOV-C2-007

**Template ID:** GOV-C2-007
**Name:** AdministrativeReportGeneratorAgent
**Category:** Cat 2 (document-generation pipeline)
**Industry:** GOV

> This document describes the implementation that ships in this repository. It is kept in sync
> with `src/`.

## Position in the AgentCore architecture

- **Agent class:** `GovC2007Agent` (`src/graph/graph.py`), aliased `Graph`; `config/agent.yaml`
  names it by dotted path.
- **L1 Base (framework base class):** `AgentBaseGraph` — direct framework inheritance
- **Composition:** Cat 2 **two-layer nested** graph (outer backbone + inner domain workflow),
  bridged by a `GraphNode` in the `main` slot.
- **Three-layer separation:**
  - **State:** flat `TypedDict` (`State(AgentState)`) — structured fields stored as JSON strings
    for msgpack safety; no Pydantic models.
  - **Node:** framework inheritance (`FunctionNode`, `execute(self, state) -> dict`, partial-dict
    returns); the `main` slot is a `GraphNode`.
  - **Graph:** composition (`register_nodes()` slot substitution on the outer; fully custom
    topology on the inner `BaseGraph`).

## Architecture overview

The fixed five-slot outer backbone is never re-wired; all domain complexity lives in an inner
`BaseGraph` reached through `ReportGenerationGraphNode` (the `main` slot).

```
Outer (GovC2007Agent : AgentBaseGraph)
  START → initialize → pre_process → main → {route} → post_process → finalize → END
                                       │                  ↑ (retry → pre_process, bounded by max_retry)
                                       ▼
            Inner (DomainWorkflowGraph : BaseGraph)
              START → intake_validation → data_extraction → section_planning
                    → report_drafting → report_rendering → END
```

- `ReportGenerationGraphNode.get_subgraph()` instantiates `DomainWorkflowGraph` with the runtime
  settings resolved by `_parent_config()`.
- `extract_input()` hands the validated narrative to the inner graph and stashes the validated
  caller contract on the bridge (`src/graph/context_bridge.py`).
- The inner graph's `get_output()` and the node's `merge_output()` are designed together
  (field-name coupling).

### Outer backbone nodes

| Node | Responsibility | Input State | Output State | Class |
|------|---------------|-------------|--------------|-------|
| initialize | schema version, session id, trust level | — | (framework metadata) | `InitializeNode` (framework default) |
| pre_process | Trust gate, disallowed-instruction screen, caller-contract validation, personal-data strip | `user_input`, `input_context` | `validated_input`, `caller_contract`, `enriched_context`, `status` | `PreProcessNode` (`FunctionNode`) |
| main | Delegate to the inner `DomainWorkflowGraph` | `validated_input`, `caller_contract` | `administrative_report`, `result`, `section_plan`, `intake_notes`, `status` | `ReportGenerationGraphNode` (`GraphNode`) |
| post_process | Output gate: refuse credential shapes and personal-data shapes; clear every output-bearing field on violation | `result`, `section_plan`, `intake_notes` | `formatted_output`, `result`, `status`, `error_log` | `PostProcessNode` (`FunctionNode`) |
| finalize | response metadata, total elapsed time | — | (framework metadata) | `FinalizeNode` (framework default) |

### Inner domain workflow nodes (`DomainWorkflowGraph`)

Linear topology — no conditional branching between domain nodes, so `add_conditional_edges()` is
not used and `route()` is never reached at run time. All are `FunctionNode` subclasses returning
partial-dict updates with `required_trust_level = TrustLevel.INTERNAL`.

| Node | Responsibility | Reads | Writes |
|------|---------------|-------|--------|
| intake_validation | Build the case record from the validated contract; record what was withheld or missing | `caller_contract` | `case_record` (JSON str), `intake_notes` (JSON str) |
| data_extraction | Bucket facts by report section; aggregate the measurement records through a finite + bounded guard | `case_record` | `extracted_facts` (JSON str) |
| section_planning | Resolve the section plan for the report type against the configured templates | `case_record`, `extracted_facts`, `report_config` | `section_plan` (JSON str) |
| report_drafting | Deterministic per-section narrative synthesis | `section_plan`, `extracted_facts`, `case_record` | `section_drafts` (JSON str) |
| report_rendering | Render the final report (template file when configured, built-in layout otherwise) | `section_drafts`, `section_plan`, `case_record`, `report_config` | `administrative_report`, `status` |

### Graphs

| Graph | Base | Role |
|-------|------|------|
| `GovC2007Agent` (`Graph`) | `AgentBaseGraph` | Outer backbone; `register_nodes()` is the only override; `add_edges()` NOT overridden |
| `DomainWorkflowGraph` | `BaseGraph` | Inner custom topology; implements every abstract method; does NOT register initialize/finalize |

### Inner / outer interaction

- `extract_input(state)` returns `state["validated_input"]` (fallback `user_input`) and calls
  `set_caller_contract(...)` on the bridge.
- `DomainWorkflowGraph._extra_initial_state()` seeds `caller_contract` and `report_config` into
  inner state. This is the only route by which structured caller data and runtime configuration
  can reach the domain nodes: the framework passes a single string into a nested graph, and a
  domain node's contract is `execute(self, state) -> dict` with no config parameter.
- Inner `get_output(state)` emits `administrative_report`, `status`, `section_plan`,
  `intake_notes`, plus tracing fields.
- `merge_output(state, sub_result)` maps **only changed keys** back to the outer delta.
  `result` carries the same value as `administrative_report` because the post_process slot and the
  output gate both read `state["result"]`; without that mapping the gated output would always be
  empty.
- **Error propagation:** `error_strategy = "propagate"` — inner exceptions are re-raised as
  `SubgraphError` (fail fast). `propagate_hitl = False`.

### State definition (`src/schemas/state.py`)

`State(AgentState)` — shared across outer and inner layers. Structured fields are JSON strings
(`to_json()` on write / `from_json()` on read) so a checkpointed run round-trips without silent
corruption.

| Field | Type | Purpose | Layer |
|-------|------|---------|-------|
| validated_input | `Optional[str]` | Personal-data-stripped narrative | outer (PreProcess) |
| caller_contract | `Optional[str]` (JSON) | Validated caller contract (report type, subject, fields, measurement records) | outer → inner (bridge) |
| report_config | `Optional[str]` (JSON) | Live report settings from `config/config.yaml` | inner (seeded) |
| administrative_report | `Optional[str]` | Final rendered report | outer (merge_output) |
| case_record | `Optional[str]` (JSON) | Case record ready for extraction | inner (IntakeValidation) |
| intake_notes | `Optional[str]` (JSON) | Validation / redaction notes | inner (IntakeValidation) |
| extracted_facts | `Optional[str]` (JSON) | Section-keyed facts + quantitative summary | inner (DataExtraction) |
| section_plan | `Optional[str]` (JSON) | Ordered section plan | inner (SectionPlanning) |
| section_drafts | `Optional[str]` (JSON) | Per-section narrative drafts | inner (ReportDrafting) |
| trace_id / correlation_id | `Optional[str]` | Tracing (framework-managed) | both |

(Shared fields — `user_input`, `status`, `session_id`, `node_history`, `error_log`, `hitl_*` — are
inherited from `AgentState`.)

**State constraints:**
- Flat TypedDict only (primitives + JSON-serializable types); structured fields are JSON strings.
- No credentials in State — the checkpoint store would carry them.
- No Pydantic models, dataclasses or arbitrary objects (not msgpack-serializable).

## Security design

### Inputs: finite, bounded, inert, fail closed

`src/services/caller_contract.py` is the single place that validates everything a caller can send,
on either channel (the free-text field, which may carry a JSON case payload, and the structured
invocation parameters). Nothing downstream re-parses raw request data.

- **Trust.** `PreProcessNode` declares `VERIFIED_EXTERNAL`, so an unverified caller is refused
  before the workflow runs. The standalone HTTP entry point establishes that trust from a Bearer
  token when `INVOKE_AUTH_TOKEN` is set.
- **Disallowed instructions.** Every string is screened for chat-template control tokens
  (`<|…|>`, `[INST]`, `<<SYS>>`) as a class, and for instruction-shaped phrases that require both
  a verb and its object — so a case note that merely mentions rules or a system is not refused.
  The screen runs on the string as received, after the personal-data strip, and after invisible
  characters are removed; none of the three passes subsumes the others. The structured channel is
  screened depth-first including mapping KEYS, after parsing, with bounded nesting.
- **Numbers.** Every caller-controlled figure goes through `parse_number`, which rejects booleans,
  non-numeric text, NaN, the infinities and out-of-range magnitudes. A non-finite figure never
  raises on its own — it makes every comparison False, so a total, a minimum and an average
  computed over a column containing one are all wrong at once. `DataExtractionNode` repeats the
  finiteness guard on aggregation and reports any excluded figure as a count.
- **Rendered strings.** Labels (report type, section keys, field names, record keys) are locked to
  an inert alphabet after a documented normalisation. Free text is whitespace-collapsed and
  length-capped: the report is Markdown, and text that cannot contain a newline cannot open a
  heading, a list item or a fenced block.
- **Refusals** name the field and never repeat the value, in the response or in the audit record.
- **Structured parameters are screened for credential shapes at the adapter, before `invoke()`.**
  The framework's output gate scans every value of every node result, and the backbone's first
  node copies the structured parameters verbatim into its own result — so a credential-shaped
  string there fails the FIRST node of the graph with nothing the caller can act on. The adapter
  calls the same framework detector on the same object, so what it refuses and what the gate
  blocks are one set by construction.

### Output invariant

The invariant printed at the foot of every generated report: **no resident personal data is stored
or reproduced, and nothing credential-shaped leaves the agent.** `PostProcessNode` enforces it
recursively over the whole released surface — the report and the operational fields released with
it — and on violation returns an error status AND clears every output-bearing field. Clearing is
the load-bearing part: the framework's output envelope falls back to `state["result"]` whatever
the status, so a gate that merely raised would still ship the ungated report inside the error
envelope.

Credential shapes are checked with the framework's own detector, widened by two patterns it does
not carry (an assignment-form secret, and the publishable key prefixes). The widening direction is
deliberate: a gate NARROWER than the framework's anywhere is a containment bypass, because the
framework then raises inside post-process and the wrapper discards the clearing.

Personal-data shapes come from one definition used in both directions — the inbound strip and the
outbound refusal read the same tuple, so they cannot drift apart, and drift would always favour
the leak.

**On the monetary precision grid:** not applicable to this template. It renders no monetary
aggregate and has no currency concept — the quantitative summary is a count, total, average,
minimum and maximum over whatever figures the office submitted, with no unit attached. The
invariant enforced at the output boundary is the personal-data one above, which is the invariant
this template actually states. Figures are rendered with thousands separators, which is also what
keeps a legitimate total from taking the shape of an identification number and failing the
agent's own boundary check.

### Audit trail

Every node emits a domain event through `emit_trace_event()` on a reachable path:
`admin_report_request_accepted` / `admin_report_request_rejected` (PreProcess), `case_record_intaken`
/ `case_record_rejected` (IntakeValidation), `facts_extracted`, `section_plan_resolved`,
`section_drafts_generated`, `administrative_report_rendered`, and `admin_report_emitted` /
`admin_report_blocked` (PostProcess). The adapter emits `input_context_credential_refused` when it
refuses structured parameters. Node lifecycle events are emitted by the framework and are never
duplicated in node code.

> **Gate behaviour by node type:**
> - `FunctionNode` subclass → the framework's `@final` gates always run; extend them only through
>   `_extra_security_gate_input()` / `_extra_security_gate_output()`.
> - `GraphNode` (the `main` slot) → deliberate no-op at the subgraph boundary; the inner nodes and
>   the outer pre/post gates apply.

## Import isolation

- [x] The template does not import the platform SDK.
- [x] Import targets: `framework/`, `shared/`, `src/` only (plus `langgraph` START/END in the
      inner graph and `fastapi`/`pydantic` in the HTTP adapter).

## Design decision record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| Base class | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | A document-generation pipeline, not an autonomous loop |
| Composition | Flat single-slot | Nested GraphNode + inner BaseGraph | **Nested** | Five domain steps exceed a single `main` node and the backbone stays fixed |
| Config route to domain nodes | Per-call config parameter | Constructor forward + state seeding | **Seeding** | A domain node takes no config parameter; a per-call read returns nothing and degrades silently to defaults |
| Structured caller data across the graph boundary | Encode into the input string | ContextVar bridge | **Bridge** | The framework forwards only a string into a nested graph, and it masks that field at every node boundary |
| Report rendering | Hard-coded format | Template file + built-in layout | **Template + built-in** | Per-department layout without a code change; deterministic when no file is present |
| Section drafting | Model call | Deterministic synthesis | **Deterministic** | The same case data always yields the same report, and the whole pipeline is testable offline; `_draft_section` is the single function to replace where a deployment wants model-authored prose |
| Personal-data handling | Single pass | Inbound strip + outbound refusal, one pattern set | **Both directions** | Defence in depth without two lists that could drift |
| Prompt governance setting | Ship a `system_prompt` in config | Omit it | **Omit** | No model is wired, so a system prompt would configure nothing — a declaration with a reader but no effect |
