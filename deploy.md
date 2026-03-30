# Bolts11 Admin Dashboard

Internal operations dashboard at admin.bolts11.com

## Deploy to Railway

This is a SEPARATE Railway service from the main app.
It shares the same Supabase database.

### Step 1 — Add files to your repo

Copy these files into Agent_Work_flow/:

```
admin_app.py                              ← root of repo
routes/admin_routes.py                    ← add to existing routes/
templates/admin_base.html                 ← add to existing templates/
templates/admin_login.html
templates/admin_requests.html
templates/admin_clients.html
templates/admin_client_detail.html
templates/admin_costs.html
```

### Step 2 — Create new Railway service

1. Railway dashboard → New Service → GitHub Repo
2. Select Agent_Work_flow
3. Settings → Start Command:
   gunicorn admin_app:app --bind 0.0.0.0:$PORT --workers 1

### Step 3 — Set env vars on the NEW service

Copy these from your existing service:
- SUPABASE_URL
- SUPABASE_SERVICE_KEY
- RESEND_API_KEY
- SECRET_KEY

Add this new one:
- ADMIN_PIN = (pick a 6-digit PIN — this is your admin password)

### Step 4 — Point admin.bolts11.com at the new service

1. New Railway service → Settings → Domains → Add custom domain
2. Type: admin.bolts11.com
3. Railway shows you a CNAME value
4. Cloudflare → DNS → Add CNAME record:
   Name: admin
   Target: <Railway CNAME value>
   Proxy: ON (orange cloud)

### Step 5 — Run the SQL migration

In Supabase SQL editor, the access_requests table was already
created in a previous migration. Verify it has these columns:
  id, name, email, phone, business_type, status,
  created_at, contacted_at, approved_at

If not, run:
  sql/access_requests.sql

### Step 6 — Test

Go to admin.bolts11.com → enter your ADMIN_PIN → you're in.

## What each page does

/requests  — See all form submissions, approve/reject/contact them
             Approving creates a client record + sends welcome email

/clients   — All active clients, job counts, status

/clients/<id> — Client detail: activity log, API cost estimate,
                resend welcome email, activate/deactivate

/costs     — API cost tracking across all clients
             Estimates based on agent_activity log
