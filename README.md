# Administrative Report Generation Agent

AI agent for generating administrative reports, built with Agentic Star.

> **Category**: Cat 2 (domain-specific document-generation pipeline)
> **Industry**: Government
> **Template ID**: GOV-C2-007

## Overview

Turns a government office's case, project or survey data into a finished administrative report —
background, findings, recommendations and next actions — written in a consistent official style
and produced in one call instead of by hand.

The officer sends the case data as structured parameters or as a JSON payload; the agent validates
every field against explicit bounds, buckets the material into report sections, aggregates the
measurement records into a quantitative summary, drafts the prose for each section, and renders the
finished report.

Resident personal data is handled in both directions. Shapes that identify a resident — the
individual identification number, e-mail addresses, telephone numbers — are rewritten out of caller
text before anything is stored, fields named for personal data are withheld wholesale, and the
output boundary refuses to release a report that still matches any of those shapes rather than
trimming it. The same applies to credential-shaped strings.

The section set is configurable per report type, so one deployment can carry the layouts of several
departments without a code change, and the report renders through a template file when one is
supplied.

Typical users are local-government offices that produce routine reports on a schedule and need the
format and the personal-data handling to be the same every time, whoever writes them.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >= 3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

`config/agent.yaml` is the static manifest (identity and compile-time requirements);
`config/config.yaml` carries the runtime parameters, including the per-report-type section
templates. See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/config.yaml` — the report template path and the section set for each report type.
2. Replace the sample payloads under `deploy/` with ones from your own forms.
3. Review the node implementations under `src/nodes/` for domain-specific logic; the request
   contract every caller field is checked against lives in `src/services/caller_contract.py`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
