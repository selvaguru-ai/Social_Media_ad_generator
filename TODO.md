# TODO - Build Order Tracking

> **DO NOT build all agents at once. Follow this order.**

## Build Order

### ✅ Phase 1: Core Infrastructure (COMPLETED)

- [x] Project skeleton with agent modules
- [x] Configuration system (`config.yaml`, `.env`)
- [x] Base agent class
- [x] Logging and utilities
- [x] Orchestrator framework
- [x] CLI runners

### 🔴 Phase 2: Prospect Agent (PRIORITY - IN PROGRESS)

**⚠️ This is the MAKE-OR-BREAK component. Prove it works first.**

- [x] Prospect agent implementation
- [x] Mock data for testing without APIs
- [x] CLI runner (`run_prospect.py`)
- [ ] **Test with real Apollo API**
- [ ] **Test with real Meta Ad Library API**
- [ ] **Validate lead quality** - do the leads make sense?
- [ ] **Tune scoring weights** in `config.yaml`
- [ ] Add more business data sources (Crunchbase, ZoomInfo)

**Current Status:**
- Agent is implemented with mock data fallback
- Ready for API testing once keys are added
- Scoring logic uses configurable weights

**Next Steps:**
1. Add Meta and Apollo API keys to `.env`
2. Run: `python run_prospect.py "sustainable fashion" --with-ad-discovery`
3. Review lead quality
4. Adjust thresholds if needed
5. Once working, proceed to Phase 3

---

### ⬜ Phase 3: Ad Discovery Agent

- [x] Ad Discovery agent implementation
- [x] Meta Ad Library integration (basic)
- [ ] Test with real API
- [ ] Add pagination for large result sets
- [ ] Improve ad pattern detection (use NLP)
- [ ] Cache results to avoid redundant API calls
- [ ] Add filtering by ad format (video, image, carousel)

**Current Status:**
- Basic implementation complete
- Mock data available for testing
- Real API integration scaffolded but untested

---

### ⬜ Phase 4: Creative Agent (Higgsfield)

**Goal: Generate one video end-to-end before moving on.**

- [x] Creative agent implementation (stub)
- [ ] **Implement real Higgsfield API integration**
- [ ] Test job submission + polling pattern
- [ ] Handle generation failures gracefully
- [ ] Add retry logic for failed generations
- [ ] Implement concurrent generation with limits
- [ ] Add video template/style customization
- [ ] Cache generated videos to avoid regeneration

**Blockers:**
- Higgsfield API access (requires paid account)
- API documentation is sparse
- Webhook support unclear

**Alternatives:**
- Use Higgsfield MCP if available
- Use a third-party aggregator
- Mock videos for testing (already implemented)

---

### ⬜ Phase 5: Contact Enrichment

- [x] Contact enrichment agent implementation (stub)
- [ ] **Implement Apollo API integration**
- [ ] Implement Hunter.io integration (fallback)
- [ ] Add LinkedIn profile scraping (if legal)
- [ ] Add confidence scoring
- [ ] Handle multiple contacts per company
- [ ] Filter by seniority level
- [ ] Add email verification

**Current Status:**
- Stub with mock data
- Ready for Apollo API integration

---

### ⬜ Phase 6: Outreach Agent

**⚠️ MUST stay behind human approval gate in v1.**

- [x] Outreach agent implementation (gated)
- [x] Message composition logic
- [x] Human approval gate (CLI)
- [ ] **Test SMTP sending** (keep disabled by default)
- [x] Add web-based approval UI (`dashboard/` + `api/`, see README)
- [ ] Add email templates
- [ ] Implement send throttling
- [ ] Add bounce handling
- [ ] Track open/click rates (if using ESP)
- [ ] Add unsubscribe handling
- [ ] Regional compliance checks (CAN-SPAM vs GDPR)

**Current Status:**
- Message drafting works
- CLI approval gate implemented
- Web approval gate implemented — decisions land in `data/approvals.json` and
  are mirrored into `data/pipeline_state.json` so a resumed run agrees with the UI
- SMTP sending stubbed but not tested
- **DO NOT enable auto-send**

**Remaining for the web gate:** `agents/outreach.py` still resolves approvals via
the CLI prompt (`_cli_approval`). To let a run block on dashboard decisions, add a
`web` branch to `_approval_gate` that polls `data/approvals.json`. Until then the
flow is: run the pipeline, then decide in the dashboard.

---

### ⬜ Phase 7: Orchestrator Polish

- [x] Basic orchestrator implementation
- [x] State persistence
- [x] Retry logic
- [ ] Better error handling per stage
- [ ] Add stage rollback capability
- [ ] Progress indicators
- [ ] Email notifications on completion/failure
- [ ] Webhooks for external integrations
- [ ] Scheduling (cron-like runs)
- [ ] Multi-niche support in single run

---

## Known Issues & Limitations

### Prospect Agent
- Mock data doesn't reflect real brand diversity
- Scoring weights need real-world tuning
- Need more business data sources beyond Apollo
- Company size filtering is basic

### Ad Discovery
- Only gets ACTIVE ads (no historical data)
- No targeting data outside EU/UK
- Rate limits can be restrictive (200/hour)
- Pattern detection is keyword-based (needs NLP)

### Creative Agent
- Higgsfield API not yet integrated (using mocks)
- No video quality control
- No customization per brand style
- Generation can be slow (polling required)

### Contact Enrichment
- Only Apollo implemented (need more sources)
- No email verification
- Confidence scoring is basic
- Limited to email/LinkedIn (no phone)

### Outreach
- SMTP sending not fully tested
- No deliverability tracking
- No A/B testing of messages
- Basic compliance checks only

---

## Testing Checklist

### Before Running Full Pipeline

- [ ] Test Prospect agent with real APIs
- [ ] Validate lead quality manually
- [ ] Test Ad Discovery API (stay under rate limits)
- [ ] Generate at least one video with Higgsfield
- [ ] Test contact enrichment with real data
- [ ] Draft messages and review for spam triggers
- [ ] Verify SMTP settings (but don't send)
- [ ] Review all compliance requirements

### Production Readiness

- [ ] All API keys in `.env` (never commit!)
- [ ] Approval gate enabled (`auto_approve: false`)
- [ ] Rate limits configured conservatively
- [ ] Logging enabled and monitored
- [ ] State persistence working (test resume)
- [ ] Error handling tested (fail gracefully)
- [ ] Compliance documentation reviewed
- [ ] Unsubscribe mechanism tested

---

## Future Enhancements

### High Priority
- [ ] Better business data sources (Crunchbase API)
- [ ] Actual Higgsfield integration
- [ ] Email verification for contacts
- [x] Web UI for approval gate

### Medium Priority
- [ ] A/B testing for message templates
- [ ] NLP for ad pattern detection
- [ ] LinkedIn InMail integration
- [ ] Deliverability monitoring

### Low Priority
- [ ] Multi-language support
- [ ] Slack/Discord notifications
- [x] Analytics dashboard (funnel, lead scores, market intel — `dashboard/`)
- [ ] Lead scoring ML model

---

## Questions to Answer

1. **Prospect Agent:**
   - What's the optimal company size range per niche?
   - How should we weight competitor presence vs. ad absence?
   - Should we exclude certain industries?

2. **Creative Agent:**
   - How long should sample videos be?
   - Should we customize by industry/brand?
   - What's acceptable generation failure rate?

3. **Outreach:**
   - What's the best sending volume per day?
   - Should we segment by region/compliance?
   - Email vs LinkedIn - which converts better?

4. **Pipeline:**
   - Should we run nightly or on-demand?
   - How to handle failed stages?
   - When to refresh lead lists?

---

**Last Updated:** [Auto-generated on scaffold]

**Current Phase:** Phase 2 - Prospect Agent (Testing Required)

**Next Milestone:** Validate Prospect agent with real APIs and tune lead scoring
