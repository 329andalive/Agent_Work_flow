"""
pwa_routes.py — Flask Blueprint for the Progressive Web App (PWA) shell

The PWA is the tech's primary interface on the road. It installs to the
home screen via the browser install prompt — no app store, no download.

Routes:
    GET /pwa/              — PWA shell (today's route, current job, status)
    GET /pwa/sw.js         — Service worker (served at root scope)
    GET /pwa/manifest.json — Web app manifest (alias to /static/manifest.json)

Future routes (not in this commit — see CLAUDE.md):
    GET /pwa/clock         — Clock in/out screen
    GET /pwa/job           — New job input
    GET /pwa/chat          — AI chat
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, render_template, send_from_directory

pwa_bp = Blueprint("pwa_bp", __name__, url_prefix="/pwa")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_static_dir = os.path.join(_project_root, "static")


# ---------------------------------------------------------------------------
# GET /pwa/ — PWA shell
# ---------------------------------------------------------------------------

@pwa_bp.route("/", strict_slashes=False)
def pwa_shell():
    """
    Serve the PWA shell template. This is what loads when a tech taps
    the home-screen icon. Subsequent screens (clock, job, chat) will
    be added in follow-up commits.

    No login required at this stage — token-based auth lands in step 2.
    """
    return render_template("pwa/shell.html")


# ---------------------------------------------------------------------------
# GET /sw.js — Service worker at root scope
# ---------------------------------------------------------------------------
# Service workers can only control pages within their scope. A SW served
# from /static/sw.js is scoped to /static/. To control /pwa/* (and any
# other root path), we need to serve sw.js from a path that includes the
# scope we want. We expose it at /sw.js (root) so it can control the
# whole app, including /pwa/, /doc/, and /static/ assets.

def register_root_sw(app):
    """Register the /sw.js root route on the Flask app (not the blueprint)."""
    @app.route("/sw.js")
    def root_sw():
        response = send_from_directory(_static_dir, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Content-Type"] = "application/javascript"
        return response
