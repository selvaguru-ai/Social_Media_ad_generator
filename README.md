# Social Media Ad Generator & Outreach Pipeline

A multi-agent system that analyzes social media ads, identifies under-advertising brands, generates sample video ads, and automates outreach to convert them into clients.

## 🎯 What It Does

This pipeline:

1. **Analyzes ads** running on Instagram/Facebook in your target niche
2. **Identifies low-spend brands** (the sales leads) - competitors that are NOT advertising heavily
3. **Generates sample video ads** for each lead using Higgsfield AI
4. **Finds brand managers** and their contact details
5. **Sends personalized pitches** with competitor intel + sample video (human-approved)

This is a **prospect-to-pitch pipeline**, not just an ad generator.

## 🏗️ Architecture

The system consists of 6 specialized agents + 1 orchestrator:

```
Orchestrator
    │
    ├─► Ad Discovery agent      → Finds frequently running ads + heavy advertisers
    ├─► Prospect agent          → Identifies low-spend brands (THE LEADS) ⚠️ CRITICAL
    ├─► Contact Enrichment      → Finds brand manager contacts
    ├─► Creative agent          → Generates sample video via Higgsfield
    └─► Outreach agent          → Composes + sends pitch (GATED)
            │
            ▼
        [ Human Approval Gate ]  ← Required before any send
```

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repo (if not already done)
git clone <repo-url>
cd <repo-directory>

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env  # or use your favorite editor
```

**Required API Keys:**
- **Meta Ad Library** (free): https://developers.facebook.com/
- **Apollo or Hunter.io** (for contact enrichment): https://apollo.io/ or https://hunter.io/
- **Higgsfield** (for video generation): https://cloud.higgsfield.ai/

> **Note:** The pipeline works with MOCK data if APIs are not configured, allowing you to test the flow.

### 3. Edit Config (Optional)

Edit `config.yaml` to customize:
- Target niche and regions
- Lead qualification thresholds
- Rate limits
- Compliance settings

### 4. Run the Prospect Agent (Start Here!)

**🔴 IMPORTANT: Test the Prospect agent first. This is the riskiest component.**

```bash
python run_prospect.py "sustainable fashion"
```

This will:
- Find brands in the niche
- Check their ad presence
- Output a ranked list of low-spend leads

**Verify the leads make sense before proceeding.**

### 5. Run the Full Pipeline

Once the Prospect agent works:

```bash
python run_pipeline.py "sustainable fashion" --regions US,GB,CA
```

This runs all 6 agents in sequence.

## 📋 Build Order (Follow This!)

Do NOT build everything at once. Follow this order:

1. ✅ **Prospect + Ad Discovery** - Prove you can find low-spend leads
2. ⬜ **Creative (Higgsfield)** - Generate one video end-to-end
3. ⬜ **Contact Enrichment + Outreach** - Build last, keep gated
4. ⬜ **Orchestrator** - Wire stages together once each works

See `TODO.md` for detailed task tracking.

## 📁 Project Structure

```
.
├── agents/                     # Agent modules
│   ├── base.py                # Base agent class
│   ├── ad_discovery.py        # Ad Discovery agent
│   ├── prospect.py            # Prospect agent (CRITICAL)
│   ├── contact_enrichment.py  # Contact finder
│   ├── creative.py            # Video generation
│   └── outreach.py            # Outreach composer (GATED)
├── utils/                     # Utilities
│   ├── config.py              # Configuration management
│   └── logger.py              # Logging setup
├── orchestrator.py            # Pipeline orchestrator
├── run_prospect.py            # Prospect agent CLI
├── run_pipeline.py            # Full pipeline CLI
├── config.yaml                # Configuration file
├── .env.example               # Environment template
├── requirements.txt           # Python dependencies
├── TODO.md                    # Build order tracking
└── README.md                  # This file
```

## 🔧 CLI Usage

### Test Prospect Agent (Start Here)

```bash
# Basic usage
python run_prospect.py "sustainable fashion"

# With options
python run_prospect.py "fitness apps" --regions US,CA --max-leads 20

# With Ad Discovery for competitor context
python run_prospect.py "pet food" --with-ad-discovery
```

### Run Full Pipeline

```bash
# Basic usage
python run_pipeline.py "sustainable fashion"

# With regions
python run_pipeline.py "pet food" --regions US,GB,CA,AU

# Resume interrupted run
python run_pipeline.py --resume
```

## 🛡️ Compliance & Safety

### Outreach Compliance

**US Contacts (CAN-SPAM):**
- ✅ Accurate sender identity
- ✅ Working unsubscribe mechanism
- ✅ Physical address in footer

**EU/UK Contacts (GDPR/ePrivacy):**
- ⚠️ Lawful basis required
- ⚠️ Cold emailing personal addresses often not permitted
- ✅ Right to erasure honored

**Deliverability:**
- Use a warmed sending domain
- Throttle send volume
- Avoid spam triggers

### Human Approval Gate

**No messages are sent automatically in v1.**

All outreach goes through a human approval gate before sending. This is controlled in `config.yaml`:

```yaml
approval:
  enabled: true           # MUST be true in production
  method: "cli"           # cli, web, or email
  auto_approve: false     # NEVER enable in production
```

## 🔑 API Setup

### Meta Ad Library (Required)

1. Go to https://developers.facebook.com/
2. Create a developer app
3. Get your access token
4. Add to `.env`:
   ```
   META_ACCESS_TOKEN=your_token_here
   ```

**Rate Limit:** ~200 calls/hour (standard access)

### Contact Enrichment (Choose One)

**Option 1: Apollo.io** (Recommended for B2B)
- Sign up: https://apollo.io/
- Get API key from Settings
- Add to `.env`

**Option 2: Hunter.io**
- Sign up: https://hunter.io/
- Get API key
- Add to `.env`

### Higgsfield (Video Generation)

1. Sign up: https://cloud.higgsfield.ai/
2. Get API credentials
3. Add to `.env`:
   ```
   HIGGSFIELD_API_KEY=your_key_here
   ```

**Note:** Higgsfield API is gated behind paid tiers. The system works with mock data if not configured.

## 📊 Output

The pipeline generates:

- **Lead lists** with qualification scores
- **Contact records** with confidence ratings
- **Video URLs** for each lead
- **Drafted messages** for review
- **Detailed JSON reports** in `data/`

Reports are saved as:
```
data/report_YYYYMMDD_HHMMSS.json
```

## 🐛 Troubleshooting

### "No qualified leads found"

- Check your niche is specific enough
- Verify company size thresholds in `config.yaml`
- Try different regions
- Lower `max_ad_spend_threshold` in config

### "Meta API error"

- Check your access token is valid
- Verify rate limits (200/hour)
- Ensure your app has Ad Library access

### "Enrichment failed"

- Verify API keys are configured
- Check rate limits for your plan
- Try alternative enrichment sources

## 🤝 Contributing

This is a prototype/POC system. Areas for improvement:

- [ ] Add more business data sources (Crunchbase, ZoomInfo)
- [ ] Implement actual Higgsfield API integration
- [ ] Add web UI for approval gate
- [ ] Improve NLP for ad pattern detection
- [ ] Add deliverability tracking
- [ ] Build retry queue for failed enrichments

## 📄 License

[Your License Here]

## ⚠️ Disclaimer

This tool is for educational and business development purposes. Users are responsible for:

- Complying with CAN-SPAM, GDPR, and local regulations
- Obtaining proper consent where required
- Respecting rate limits and Terms of Service
- Ensuring ethical use of contact data

**Do not use this for spam or unsolicited bulk email.**

## 📞 Support

For questions or issues:
- Open an issue in this repo
- Check `TODO.md` for known limitations
- Review agent logs in `logs/`

---

Built with ❤️ for growth teams
