# Contributing to Bolts11

## Client Data Policy

Never commit client-specific data to this repo. All client data lives in Supabase.

This includes:
- Client IDs (UUIDs)
- Business names
- Owner names
- Phone numbers (business, personal, Telnyx)
- Addresses or service areas
- Customer names or contact info
- Personality layer documents

### Where client data belongs
- **Supabase** — clients, customers, employees, jobs tables
- **`.env`** — API keys, Telnyx phone number, Supabase credentials
- **`sql/local/`** — local-only seed scripts (gitignored)

### What belongs in the repo
- Platform-wide seed data: trade verticals, pricing benchmarks, onboarding templates
- Generic test fixtures with placeholder data (`tests/conftest.py`)
- Example/template personality docs (not real client data)

### Test data
Use generic placeholders in test fixtures:
- Client ID: `00000000-0000-0000-0000-000000000001`
- Phone: `+15555550100`
- Business: `Test Trades Co`
- Never use real names, addresses, or phone numbers in tests
