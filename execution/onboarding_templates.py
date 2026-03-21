"""
onboarding_templates.py — DEPRECATED FALLBACK ONLY

Pricing is now served from the pricing_benchmarks table in Supabase.
These hardcoded templates are used only if the database query fails.
Update the database, not this file.

See: execution/db_pricing.py for the live query functions.
See: routes/onboarding_routes.py for the fallback logic.

Usage (fallback only):
    from execution.onboarding_templates import get_template
    template = get_template("septic")
"""

PRICING_TEMPLATES = {
    "septic": [
        {"service": "Pump-out — 1,000 gal tank", "low": 225, "high": 275},
        {"service": "Pump-out — 1,500 gal tank", "low": 275, "high": 325},
        {"service": "Pump-out — 2,000 gal tank", "low": 325, "high": 400},
        {"service": "Baffle replacement", "low": 150, "high": 250},
        {"service": "Tank inspection", "low": 100, "high": 150},
        {"service": "Riser installation", "low": 200, "high": 350},
        {"service": "Travel (per mile over 20)", "low": 2, "high": 3},
    ],
    "plumbing": [
        {"service": "Service call / diagnosis", "low": 95, "high": 150},
        {"service": "Drain cleaning — standard", "low": 150, "high": 225},
        {"service": "Water heater replacement", "low": 800, "high": 1400},
        {"service": "Toilet repair", "low": 95, "high": 175},
        {"service": "Faucet replacement", "low": 125, "high": 225},
        {"service": "Emergency after-hours", "low": 195, "high": 275},
    ],
    "hvac": [
        {"service": "AC tune-up / service", "low": 89, "high": 149},
        {"service": "Furnace tune-up", "low": 89, "high": 149},
        {"service": "AC repair — diagnosis", "low": 95, "high": 150},
        {"service": "Filter replacement", "low": 25, "high": 75},
        {"service": "Duct cleaning", "low": 300, "high": 600},
        {"service": "New system install", "low": 3500, "high": 8000},
    ],
    "electrical": [
        {"service": "Service call / diagnosis", "low": 95, "high": 150},
        {"service": "Outlet / switch replacement", "low": 75, "high": 150},
        {"service": "Panel upgrade", "low": 1500, "high": 3500},
        {"service": "Lighting installation", "low": 125, "high": 250},
        {"service": "EV charger installation", "low": 500, "high": 1200},
        {"service": "Generator hookup", "low": 800, "high": 2000},
    ],
    "excavation": [
        {"service": "Site prep — per hour", "low": 125, "high": 175},
        {"service": "Septic system install", "low": 8000, "high": 15000},
        {"service": "Driveway grading", "low": 500, "high": 1500},
        {"service": "Foundation excavation", "low": 2000, "high": 6000},
        {"service": "Utility trench — per ft", "low": 15, "high": 30},
    ],
    "drain": [
        {"service": "Drain cleaning — standard", "low": 150, "high": 225},
        {"service": "Camera inspection", "low": 150, "high": 300},
        {"service": "Hydro jetting", "low": 300, "high": 600},
        {"service": "Root removal", "low": 250, "high": 500},
        {"service": "Emergency after-hours", "low": 200, "high": 350},
    ],
    "general": [
        {"service": "Service call / labor — hr", "low": 75, "high": 125},
        {"service": "Material markup (%)", "low": 15, "high": 25},
    ],
}

# Map trade vertical names from the wizard to template keys
VERTICAL_MAP = {
    "Septic & Sewer": "septic",
    "Plumbing": "plumbing",
    "HVAC": "hvac",
    "Electrical": "electrical",
    "Excavation": "excavation",
    "Drain Cleaning": "drain",
    "General Contracting": "general",
    "Other": "general",
    # Lowercase aliases
    "septic": "septic",
    "plumbing": "plumbing",
    "hvac": "hvac",
    "electrical": "electrical",
    "excavation": "excavation",
    "drain": "drain",
    "general": "general",
}

# Specialties per trade vertical (for Step 2 multi-select)
TRADE_SPECIALTIES = {
    "septic": [
        "Pump-outs", "Inspections", "Repairs",
        "New Installations", "Risers", "Baffle Replacement",
    ],
    "plumbing": [
        "Repairs", "New Construction", "Water Heaters",
        "Fixtures", "Drain Cleaning", "Emergency",
    ],
    "hvac": [
        "AC Service", "Heating Service", "Installation",
        "Duct Work", "Heat Pumps", "Emergency",
    ],
    "electrical": [
        "Residential", "Commercial", "Panel Upgrades",
        "Generators", "EV Chargers", "Emergency",
    ],
    "excavation": [
        "Site Prep", "Septic Systems", "Driveways",
        "Foundations", "Utilities", "Land Clearing",
    ],
    "drain": [
        "Drain Cleaning", "Camera Inspection", "Hydro Jetting",
        "Root Removal", "Sewer Line Repair", "Emergency",
    ],
    "general": [
        "Residential", "Commercial", "Remodeling",
        "Repairs", "New Construction",
    ],
}


def get_template(vertical: str) -> list:
    """Get pricing template for a trade vertical."""
    key = VERTICAL_MAP.get(vertical, "general")
    return PRICING_TEMPLATES.get(key, PRICING_TEMPLATES["general"])


def get_specialties(vertical: str) -> list:
    """Get specialty options for a trade vertical."""
    key = VERTICAL_MAP.get(vertical, "general")
    return TRADE_SPECIALTIES.get(key, TRADE_SPECIALTIES["general"])
