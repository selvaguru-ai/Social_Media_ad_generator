# SPEC.md — Social Media Ad Generator & Outreach Pipeline

> Paste-ready brief for a coding agent (e.g. Cursor). Read this file top to
> bottom, then scaffold the project starting with the **Prospect agent** (the
> riskiest component). Do not build all agents at once — follow the build order
> in the last section.

---

## 1. Goal

Build a multi-agent system that:

1. Analyzes ads frequently running on Instagram / Facebook in a target niche.
2. Identifies **competitor brands that are NOT spending much on ads** (these are
   the sales leads — under-advertising brands that could be converted).
3. Generates a **sample video ad** for each lead using **Higgsfield**.
4. Finds the brand manager's contact details and sends an outreach message
   containing competitor intel + the sample video — **behind a human-approval
   gate**.

This is a prospect-to-pitch pipeline, not just an ad generator.

---

## 2. Architecture overview

```
User input (niche / seed brand / region)
        │
        ▼
   ┌─────────────┐
   │ Orchestrator │  runs pipeline, passes state, handles retries
   └─────────────┘
        │
        ├─► Ad Discovery agent      → what's frequently running + heavy advertisers
        ├─► Prospect agent          → low-spend brands (the leads)  ⚠ hardest
        ├─► Contact Enrichment agent→ brand manager + email/LinkedIn
        ├─► Creative agent          → sample video via Higgsfield
        └─► Outreach agent          → compose + send pitch
                    │
                    ▼
            [ Compliance / human-approval gate ]  ← required before any send
                    │
                    ▼
            Final report + sent pitches
```

**Count: 6 agents + 1 orchestrator + 1 compliance gate.**

---

## 3. Agent specifications

### 3.1 Orchestrator
- **Role:** Owns the pipeline. Sequences agents, passes state between them,
  handles retries/back-off, logs each stage.
- **Input:** target niche, optional seed brand(s), target region.
- **Output:** consolidated run report; triggers each downstream agent.
- **Notes:** Keep state in a simple store (SQLite / JSON) so runs are resumable.

### 3.2 Ad Discovery / Market agent
- **Role:** Pull what's frequently running on IG/FB in the niche; identify the
  dominant advertisers and recurring ad patterns (hooks, formats, offers).
- **Data source:** Meta Ad Library API (`ads_archive` endpoint via
  `graph.facebook.com`). Free; requires a Meta developer app + access token.
- **Constraints (important):**
  - Rate limit ~200 calls/hour/app (standard access). Add a queue + back-off.
  - Coverage is strong for EU commercial ads and political/issue ads; non-EU
    commercial ads are more restricted.
  - For most commercial categories the API returns **currently active ads only**
    (no historical creative).
  - No targeting data outside EU/UK (DSA exception).
- **Output:** structured records of active ads per advertiser (creative, copy,
  format, impression bucket where available).

### 3.3 Prospect agent ⚠ (build this first)
- **Role:** Find the **low-spend** brands — the actual leads. This is inverted
  from what the Ad Library does well (the library surfaces who IS advertising).
- **Approach:** Cross-reference a business/brand database (e.g. Apollo,
  Crunchbase, or a niche-specific list) **against** Ad Library presence, and
  surface brands in the category with little or no active ad footprint.
  - Use Meta's spend/impression signals where available (12-month cumulative
    spend for a subset of commercial brands; "Low Impression Count" badge for
    sub-100-impression ads; impressions filter).
- **Output:** ranked list of candidate leads with a "why they qualify" note
  (e.g. "active company, competitors advertising heavily, near-zero ad
  presence").
- **This is the riskiest assumption — prove it works before building the rest.**

### 3.4 Contact Enrichment agent
- **Role:** For each qualified lead, find the brand/marketing manager and a
  contact channel (email / LinkedIn).
- **Data source:** enrichment API (Apollo, Hunter, Clearbit, or similar).
- **Output:** contact record per lead. Flag confidence + source for each.

### 3.5 Creative agent (Higgsfield)
- **Role:** Generate a short sample video ad tailored to each lead.
- **Access options (pick one):**
  - **Higgsfield MCP** — cleanest for an agent to drive directly. Preferred.
  - Higgsfield Cloud API (`cloud.higgsfield.ai`) — Python/Node SDKs.
  - A third-party aggregator that wraps Higgsfield with webhooks + async polling.
- **Constraints:** native API is gated behind paid tiers, docs are sparse, rate
  limits undocumented, credits may expire, limited/no native webhooks. Use the
  **POST-then-poll with exponential back-off** pattern; treat generation as
  async (submit job → poll status → download output URL when `COMPLETED`).
- **Output:** hosted MP4 URL per lead + the prompt/brief used.

### 3.6 Outreach agent
- **Role:** Compose a personalized pitch (competitor intel + sample video link)
  and send it to the brand manager.
- **Output:** drafted message per lead; sends only after the compliance gate.

### 3.7 Compliance / human-approval gate (required)
- No message sends automatically in v1. A human reviews and approves each batch.
- **US contacts:** CAN-SPAM (accurate sender identity, working opt-out/unsubscribe).
- **EU/UK contacts:** GDPR / ePrivacy — much stricter; a lawful basis is required
  and cold-emailing personal addresses is often not permitted. Segment by region
  and apply the correct rules; when unsure, don't send.
- **Deliverability:** use a warmed sending domain, throttle volume, avoid
  spammy patterns — automated cold email from a fresh domain will be filtered.

---

## 4. Suggested tech stack (adjust as needed)

- **Language:** Python (async-friendly; good SDK coverage for the APIs above).
- **Orchestration:** a lightweight agent framework (LangGraph / CrewAI /
  custom async orchestrator) — start simple, don't over-engineer.
- **State:** SQLite or JSON files for run state and lead records.
- **Secrets:** `.env` for API keys (Meta, enrichment, Higgsfield, email sender).
  Never commit secrets.
- **Config:** one `config.yaml` for niche, region, rate limits, thresholds.

---

## 5. Build order (do NOT build all at once)

1. **Prospect agent + Ad Discovery** — prove you can reliably surface low-spend
   leads. This is the make-or-break assumption. Ship a CLI that outputs a lead
   list before anything else.
2. **Creative agent (Higgsfield)** — get one end-to-end video generated from a
   prompt.
3. **Contact Enrichment + Outreach** — build last, and keep them **behind the
   human-approval gate**.
4. **Orchestrator** — wire the stages together once each works standalone.

---

## 6. First task for the coding agent

Scaffold the repo:
- Project skeleton with a module per agent (`agents/prospect.py`,
  `agents/ad_discovery.py`, etc.), an `orchestrator.py`, `config.yaml`,
  `.env.example`, and a `README.md`.
- Implement the **Prospect agent** first as a runnable CLI that takes a niche +
  region and outputs a ranked lead list (stub the external APIs with clearly
  marked mock data until keys are added).
- Add a short `TODO.md` tracking the build order above.

Do not implement outreach sending yet — leave a clear `# TODO: gated behind
human approval` marker where it will go.
