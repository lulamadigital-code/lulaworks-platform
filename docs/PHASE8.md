# LulaWorks Platform — Phase 8 (AI Orchestration — Lulama)

Status: **complete** — the final phase. Companion to PHASE1–7. Architecture source: prototype `docs/AI_PLATFORM.md` (Module 11).

**Not a chatbot — a team of specialised AI employees**, each with one job, coordinated by an orchestrator (Lulama). Provider-agnostic, transparent, auditable, explainable, and **always under human control**. Built **deterministic-first**: agents ground their answers in the real modules (Phases 3–7) for free and exact; the metered LLM (Phase 2 gateway) is optional enrichment.

## Lulama — the AI Operations Director (§3)
Users don't juggle seven agents; they talk to **Lulama**. Lulama **decomposes** a request, **dispatches** the agents the user is permitted to run, **aggregates** their grounded results, and returns **ONE consolidated draft** for human review. It never commits the side-effects itself.

> *"Prepare this shutdown project"* → plan `[rfq, procurement, estimating, compliance, project, commercial]` → one draft: RFQ summary · top supplier · approved estimate at 20% margin · **compliance NOT ready (7 items open)** · 0% progress · commercial forecast — confidence 0.88.

## The specialised agents (§4) — one job each, grounded in their module
`rfq` · `procurement` · `estimating` · `compliance` · `project` · `commercial` · `executive`. Each returns a **Confidence-Engine** result: summary + findings + **confidence + sources + assumptions** + proposed actions. No hallucination — every number comes from the real modules.

## The agent security model (§7 — the critical hardening)
An AI agent executes **strictly within the invoking user's tenant context and RBAC**. Enforced at two layers:
- **Agent gate:** an agent whose `required_perm` the user lacks is *skipped* and reported in `omitted_agents` — no silent privilege escalation.
- **Tool registry:** every data read goes through a **least-privilege, tenant-scoped, audited** tool (`run_tool` checks the user's permission, relies on the ambient tenant, and writes an audit row; denials audit too).

Proven: an ops user **without `finance.view_money`** asking "prepare this project" gets the five operational agents — the **commercial agent is withheld** (*"requires 'finance.view_money'"*). An agent can never read another tenant's data, and never do what its user can't.

## AI governance (§9 — the human-approval boundary, applied to AI)
> The AI **may**: suggest · draft · summarise · recommend · predict.
> The AI **may never**: approve payments · award contracts · delete data · approve compliance · **send documents** — without human approval.

Side-effecting intents are surfaced as **proposals** (`requires_approval: true`, `executed_by_ai: false`) — never executed. Proven: *"Send the invoice and approve payment now"* → both proposed for human approval, and the invoice stays `draft`. Approving a draft **records the human's acceptance only** — actual actions run via each module's own permission-checked endpoints. One consistent safety spine across humans and AI.

## Audit · prompts · credits (§6, §8)
- **`AIInteraction`** — every Lulama run is a DRAFT with prompt version, provider, confidence, approval status; **rejected drafts are kept** for the prompt learning loop.
- **Prompt Library** — versioned (`PromptTemplate`), never hardcoded; the interaction records which version produced it.
- **Credits** — the deterministic/grounded path is **free** (proven: 500 credits unchanged after orchestration); only live-LLM enrichment meters through the gateway, which fails closed with no credits.

## API
`POST /ai/interactions/ask/` (Lulama, `ai.generate`) · `POST /ai/interactions/{id}/decision/` (approve/reject) · `GET /ai/dashboard/` (credits, agent activity, review queue, least-privilege tool list).

## Testing
**12 tests** (135 total): orchestrator consolidated draft grounded in real modules, aggregated confidence, **agent skipped without permission**, **tool refuses without permission (audited)**, least-privilege tool list, **governance proposes-but-never-sends an invoice**, decision records without executing, prompt versioning, **deterministic path consumes 0 credits**, ask/decision API flow, ask requires `ai.generate`, tenant isolation. Ruff clean; container-validated end-to-end.

## The platform is complete
All 8 phases of the Implementation Directive are built, tested, and container-validated — **16 bounded-context apps, 135 tests**. The full business loop runs end to end, with Lulama over the top:

```
RFQ → Estimate → Quotation → award → Project → compliance gate → execution →
actuals → Finance (profit + forecast) → Lulama orchestrates it all, human approves.
```

## Live LLM — wired (2026-07-16)
The Claude provider is **wired end to end**: the `anthropic` SDK ships in the image, and
Lulama gains an optional **grounded executive briefing** — the metered gateway sends the
agents' *deterministic* findings to the LLM, which only phrases them (never invents facts
or numbers). Provider fallback order (claude→openai→gemini), metered against the credit
ledger, with graceful degradation to the deterministic result on no-key / no-credits /
provider error — all stub-tested (3 tests). To go live, set `ANTHROPIC_API_KEY` +
`AI_PROVIDER=claude` (see DEPLOYMENT.md §6). Default (no key) stays deterministic and free.

## Honestly deferred (platform-wide, noted not hidden)
pgvector similarity for Project DNA, Celery-parallel agent dispatch (agents run
synchronously today), the Flutter app, and AWS/ECS deployment (container-first foundation
is ready). The deterministic spine is complete and real; the live LLM path is wired and
stub-tested end to end (a real API key exercises it against Anthropic).
