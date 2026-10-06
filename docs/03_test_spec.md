# Test Specification — GOV-C2-007

## Strategy

Every test in this repository is deterministic: no model call, no network, no clock dependence
beyond a rendered timestamp. The suite runs against the installed framework wheel.

```bash
python -m pytest tests/ -v
```

Current result: **276 passed, 2 skipped** (the two skips are the human-review interrupt cases,
which are conditional on `hitl.enabled: true` in `config/config.yaml` — this template does not
enable it).

Three layers:

| Layer | Location | What it proves |
|---|---|---|
| Unit | `tests/unit/` | Each node's contract in isolation, plus the request-contract rules and the runtime-config path |
| Boundary | `tests/proof_of_boundary/` | The invariants at the two boundaries — what a caller may send, and what may be released |
| End to end | `tests/proof_of_boundary/test_pb_invoke_endpoint.py` | The whole stack through the real HTTP entry point, the way an external caller reaches it |

## Framework compliance

| TC-ID | Test | Expected result | Where |
|-------|------|----------------|-------|
| TC-01 | State is a flat TypedDict — no Pydantic model, no dataclass | Round-trips through JSON | `tests/proof_of_boundary/test_state_safety.py`, `tests/unit/test_state_roundtrip.py` |
| TC-02 | Invalid input is refused rather than processed | Error status, nothing carried forward | `tests/proof_of_boundary/test_request_boundary.py` |
| TC-03 | No credential reaches State | Enforced by CI's credential scan over the repository | CI |
| TC-04 | Runtime configuration arrives from `config/config.yaml`, not from a per-call parameter | The declared value reaches the inner graph | `tests/unit/test_runtime_config.py` |
| TC-05 | No duplicate lifecycle events inside `execute()` | `node_start` / `node_complete` / `node_error` are emitted by the framework only | `scripts/check_audit_trace.py` + review |
| TC-06 | The default input gate is not overridden | `TypeError` at class definition if it were | `tests/unit/test_framework_compliance_tc06_tc07.py` |
| TC-07 | The default output gate is not overridden | `TypeError` at class definition if it were | `tests/unit/test_framework_compliance_tc06_tc07.py` |
| TC-08 | `required_trust_level` is enforced | An under-privileged caller is refused before `execute()` | `tests/proof_of_boundary/test_pb_invoke_order.py` |
| TC-09 | The template owns its input guarantees | Refusal proven by calling `execute()` directly, with no framework wrapper in front | `tests/proof_of_boundary/test_request_boundary.py` |
| TC-10 | The template owns its output guarantees | The output gate refuses and clears, proven at unit and full-invoke level | `tests/unit/test_post_process_node.py`, `tests/proof_of_boundary/test_pb_invoke_endpoint.py` |
| TC-11 | Every boundary node emits a domain audit event on a reachable path | Gate passes; verified by deleting one emit and confirming it fails | `scripts/check_audit_trace.py` |

## Proof-of-boundary tests

| PB-ID | Boundary | Test | Expected result | Where |
|-------|----------|------|----------------|-------|
| PB-1 | Node → audit trail | A domain event fires on every invocation path | No silent path | `scripts/check_audit_trace.py` |
| PB-2 | State serialization | Post-invoke State carries primitives only | No object survives into a checkpoint | `tests/proof_of_boundary/test_state_safety.py` |
| PB-4 | Import isolation | The template does not import the platform SDK | AST scan finds none | `tests/proof_of_boundary/test_import_isolation.py` |
| PB-5 | Checkpoint safety | No credential, no model object in the checkpoint | Inspection passes | `tests/proof_of_boundary/test_state_safety.py` |
| PB-6 | Invoke execution order | `__call__()` runs the trust gate → `node_start` → input gate → `execute()` → output gate → `node_complete`, for every node; and the backbone runs its five slots in order | Order verified | `tests/proof_of_boundary/test_pb_invoke_order.py`, `tests/proof_of_boundary/test_pb_invoke_endpoint.py` |
| PB-7 | Human-review interrupt | Conditional on `hitl.enabled: true` | Skipped — this template does not enable it | `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` |

## Request boundary (`tests/proof_of_boundary/test_request_boundary.py`, `tests/unit/test_caller_contract.py`)

| ID | Case | Expected result |
|---|---|---|
| RB-01 | Empty, whitespace-only or non-string input | Refused, no crash |
| RB-02 | Instruction-shaped payload (override, role reassignment, prompt disclosure) | Refused by the template itself, called directly with no framework wrapper |
| RB-03 | Chat-template control tokens `<\|…\|>`, `[INST]`, `<<SYS>>` | Refused as a class, not as individual phrases |
| RB-04 | A directive split by an identifier-shaped run or by a markup tag | Refused — the screen sees the re-assembled sentence |
| RB-05 | A directive hidden with zero-width characters | Refused |
| RB-06 | A control token inside a nested structured field, and inside a mapping KEY | Refused — the screen walks the payload depth-first after parsing |
| RB-07 | Ordinary administrative prose mentioning rules, guidance, a system, or "act as a proxy" | Accepted — the fail-closed direction is the one that blocks real work |
| RB-08 | `NaN`, `Infinity`, `-Infinity` as text and as raw JSON floats, per numeric field | Refused, naming the field |
| RB-09 | A boolean where a figure belongs | Refused rather than counted as 1 or reinterpreted as text |
| RB-10 | An out-of-range magnitude | Refused |
| RB-11 | A field name outside the inert alphabet | Refused, and the name is not echoed |
| RB-12 | Over-length text, too many fields, too many records, nesting beyond the cap | Refused |
| RB-13 | Personal-data shapes in caller text | Rewritten out before storage; ordinary dates, percentages and grouped figures untouched |
| RB-14 | A rejected value | Never echoed into the error log or the response |
| RB-15 | Structured parameters over the size cap | Refused at the adapter with 413 |
| RB-16 | A credential-shaped structured value | Refused at the adapter with 400, naming the field, never the value |
| RB-17 | A hostile field NAME carrying a credential | Reported by position, not echoed |
| RB-18 | Ordinary case text on the same field | Still accepted |

## Output boundary (`tests/proof_of_boundary/test_output_boundary.py`, `tests/unit/test_post_process_node.py`)

| ID | Case | Expected result |
|---|---|---|
| OB-01 | Credential shapes the framework detector knows (stripe, `sk-`, AWS, connection string, token, bearer) | Named by the gate |
| OB-02 | Credential shapes the framework detector does not carry (assignment form, publishable key prefix) | Named by the gate — it widens the framework set, never narrows it |
| OB-03 | Personal-data shapes | Named by the gate |
| OB-04 | A leak nested inside a structured value | Found — the scan walks nested structures |
| OB-05 | A clean report and a clean nested value | Not flagged — the control that proves the scan is not always positive |
| OB-06 | A violation | Error status, every output-bearing field cleared, the fixed notice in the two surfaced fields |
| OB-07 | The error envelope on a violation | No released text, no traceback, no source path |
| OB-08 | A leak in an operational field released alongside the report | Blocked — the operational fields are gated with the report |
| OB-09 | A clean run | Passes through unchanged; the operational fields are not cleared |
| OB-10 | Personal data submitted in case text | Never reaches the rendered report; the redaction marker is visible instead |

## Business logic

| ID | Case | Expected result | Where |
|---|---|---|---|
| BL-01 | A full case payload | A report carrying the caller's own subject, department, field text and recommendation | `test_pb_invoke_endpoint.py` |
| BL-02 | Different case data | A different report — not a fixed baseline | `test_pb_invoke_endpoint.py` |
| BL-03 | Measurement records | Aggregated into the quantitative summary and rendered with thousands separators | `test_pb_invoke_endpoint.py`, `tests/unit/test_data_extraction_node.py` |
| BL-04 | No structured case data, narrative only | Degrades to a narrative report rather than failing | `test_pb_invoke_endpoint.py` |
| BL-05 | A section template declared in `config/config.yaml` | Visibly changes the rendered report | `test_pb_invoke_endpoint.py`, `tests/unit/test_runtime_config.py` |
| BL-06 | A section template for another report type | Not applied; the built-in set is used | `tests/unit/test_section_planning_node.py` |
| BL-07 | A malformed section template or a template path that walks out of the repository | Dropped; the agent still compiles and renders | `tests/unit/test_runtime_config.py` |
| BL-08 | A required section with no drafted body | Renders with a placeholder rather than disappearing | `tests/unit/test_report_rendering_node.py` |
| BL-09 | Every key declared in `config/config.yaml` | Has a live reader — no dead declaration | `tests/unit/test_runtime_config.py` |
