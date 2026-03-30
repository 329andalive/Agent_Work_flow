"""
admin_app.py — Bolts11 Admin Dashboard
Runs as a SEPARATE Flask app on a separate Railway service.
Domain: admin.bolts11.com

Start command: gunicorn admin_app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60
Env vars needed: SUPABASE_URL, SUPABASE_SERVICE_KEY, RESEND_API_KEY, SECRET_KEY, ADMIN_PIN
"""

import os
import sys
from datetime import timedelta
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, template_folder="templates")

app.secret_key = os.environ.get("SECRET_KEY", "dev-admin-secret-change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_NAME"]        = "bolts11_admin_session"
app.config["SESSION_COOKIE_SECURE"]      = True
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"

from routes.admin_routes import admin_bp
app.register_blueprint(admin_bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=False)
