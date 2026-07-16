# LulaWorks Platform — Phase 2 (RFQ Intelligence)

Status: **complete.** Companion to `docs/PHASE1.md`. Architecture source: prototype `docs/RFQ_INTELLIGENCE.md`, `docs/AI_PLATFORM.md`, `docs/ARCHITECTURE.md §7`.

RFQ Intelligence turns unstructured RFQ/PO documents into trusted structured data that starts the RFQ-first lifecycle — with a human always approving before anything becomes real.

## The pipeline (end-to-end, container-proven)
```
upload PDF → store (StorageFile) → extract (deterministic, then AI fallback) →
Confidence Engine (per-field) → human review (edit) → human approve →
Quotation + Project DNA → Knowledge capture
```

## Apps added
| App | Delivers |
|---|---|
| `rfq` | `RFQDocument` (pipeline states), `ExtractedField` (Confidence Engine), `RFQLineItem`; deterministic extractor (real SA Coupa layout); AI-fallback orchestration; upload/review/approve API |
| `knowledge` | `ProjectDNA` (minted from approved data); **3-tier Knowledge Platform** (private / shared-entity / aggregate) + promotion pipeline; Client/Mine/WorkType profiles |
| `ai_platform` (extended) | provider adapters (Claude/OpenAI/Gemini), versioned `PromptTemplate` registry |

## Extraction — deterministic-first, AI fallback
- **Deterministic** (`rfq/extraction.py`): validated on the real Sibanye/Western Platinum Coupa layout — `PO NUMBER`, `DATE yyyy/mm/dd`, SA number formats (`29 160,00`). Free, exact, no AI credits.
- **OCR fallback**: no text layer (scanned) → Tesseract (`pytesseract` + `pdf2image`, container `tesseract-ocr`/`poppler-utils`), lazy-imported.
- **AI fallback** (`rfq/intelligence.py`): if a provider is configured and gaps remain (missing critical fields / no lines), the metered gateway calls the LLM; deterministic values always win; AI fields are marked + confidence-scored. **Off by default** — runs fully deterministic until a key is set.
- **Confidence Engine**: every field stores value + approved_value + confidence + method + source + review status. Below 85% → `needs_review`.

## Human-approval boundary (enforced + tested)
Nothing auto-approves. Upload → `in_review`; a user with `rfq.approve` confirms → the Quotation and Project DNA are created. Upload needs `rfq.upload`.

## Knowledge Platform — the moat (ARCHITECTURE §7)
Three tiers, one-way promotion, learns without leaking:
- **Private** (default): `ClientProfile`/`MineProfile`/`WorkTypeTemplate` — `TenantBaseModel`, auto-scoped, never crosses tenants.
- **Shared-entity** (opt-in): `SharedEntityFact` — de-identified facts about mines/clients; source company **never exposed**; corroborated across distinct companies.
- **Aggregate-only** (opt-in): stats exposed only past **k-anonymity `MIN_N=5`**; individual samples never revealed.
- Contribution is **opt-in per tenant** (`KnowledgeConfig`, default off); promotion strips the source; captured on RFQ approval.

## Activating live AI
Set `ANTHROPIC_API_KEY` (env / Secrets Manager) and `pip install anthropic` (or `openai` / `google-generativeai` for those providers). No code change — the gateway picks it up. Metered against the tenant's AI credit ledger.

## Testing
**70 tests** (local + in-container): SA-number parsing, deterministic extraction, OCR fallback wiring, upload+RBAC, approve→Quotation+Project DNA, no-auto-approve, tenant isolation, review edits, AI enrichment (stub) + metering + not-configured, Knowledge 3-tier confidentiality (private isolation, opt-in promotion, source de-identification, corroboration, aggregate k-anonymity). Ruff clean.

## Verified end-to-end (HTTP, in container)
Upload real-layout PDF → 4 fields + 3 lines extracted → approve → `QT-2026-000001` (total 11 293) → Project DNA (client, materials, value) minted.

## Known limitations / next
- Live LLM path exercised only with a key (framework tested via stub).
- Project DNA semantic similarity (pgvector) deferred until the extension is provisioned.
- Document **classification** (routing any doc type) — the generic engine is designed; RFQ is the built consumer.
- Next: **Phase 3 — Procurement** (suppliers, supplier RFQs, comparison, POs, GRN, 3-way match).
