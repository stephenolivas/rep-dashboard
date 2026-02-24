#!/usr/bin/env python3
"""
Fetch Closed/Won opportunities from Close CRM and generate data.json
for the GitHub Pages sales dashboard.

Usage:
    CLOSE_API_KEY=your_key python3 scripts/fetch_data.py

The script:
1. Queries the Close API for Closed/Won opportunities in the current month
2. Maps user_ids to rep names
3. Calculates MTD revenue and deal counts per rep
4. Writes data.json to the repo root
"""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from base64 import b64encode
from calendar import monthrange

# ─── Configuration ───────────────────────────────────────────────────────────

CLOSE_API_KEY = os.environ.get("CLOSE_API_KEY", "")
BASE_URL = "https://api.close.com/api/v1"

PIPELINE_ID = "pipe_78hyBUVS7IKikGEmstObu1"
CLOSED_WON_STATUS_ID = "stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03"

# Monthly quotas per rep
REP_QUOTAS = {
    "Christian Hartwell": 100_000,
    "Lyle Hubbard": 100_000,
    "Ategeka Musinguzi": 100_000,
    "Scott Seymour": 100_000,
    "Eric Piccione": 100_000,
    "Jordan Humphrey": 75_000,
    "Jason Aaron": 75_000,
    "Robin Perkins": 75_000,
    "William Chase": 75_000,
    "Ryan Jones": 50_000,
    "John Kirk": 50_000,
}

EXCLUDE_USERS = {"Kristin Nelson"}


def api_get(endpoint, params=None):
    """Make an authenticated GET request to the Close API."""
    url = f"{BASE_URL}{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{query}"

    auth = b64encode(f"{CLOSE_API_KEY}:".encode()).decode()
    req = Request(url, headers={
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
    })

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error {e.code}: {body}", file=sys.stderr)
        raise


def fetch_org_users():
    """Fetch all users in the org and return a dict of user_id -> full name."""
    data = api_get("/user/")
    users = {}
    for u in data.get("data", []):
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        full = f"{first} {last}".strip()
        users[u["id"]] = full
    return users


def fetch_closed_won_opportunities(year, month):
    """
    Fetch all Closed/Won opportunities in the given month from the Sales Pipeline.
    Uses pagination to handle any number of results.
    """
    _, last_day = monthrange(year, month)
    date_gte = f"{year}-{month:02d}-01"
    date_lte = f"{year}-{month:02d}-{last_day:02d}"

    all_opps = []
    skip = 0
    limit = 100

    while True:
        params = {
            "status_id": CLOSED_WON_STATUS_ID,
            "date_won__gte": date_gte,
            "date_won__lte": date_lte,
            "_skip": str(skip),
            "_limit": str(limit),
        }
        data = api_get("/opportunity/", params)
        opps = data.get("data", [])
        all_opps.extend(opps)

        if not data.get("has_more", False):
            break
        skip += limit

    # Filter to our pipeline only
    return [o for o in all_opps if o.get("pipeline_id") == PIPELINE_ID]


def build_dashboard_data():
    """Main logic: fetch data, compute metrics, return structured dict."""
    if not CLOSE_API_KEY:
        print("ERROR: CLOSE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    print(f"Fetching data for {year}-{month:02d}...")

    # Step 1: Get user map
    print("  Fetching org users...")
    user_map = fetch_org_users()

    # Step 2: Get closed/won opportunities this month
    print("  Fetching Closed/Won opportunities...")
    opps = fetch_closed_won_opportunities(year, month)
    print(f"  Found {len(opps)} Closed/Won opportunities in pipeline.")

    # Step 3: Aggregate per rep
    rep_data = {}
    seen_leads = set()  # Track lead_id to count one deal per lead

    for opp in opps:
        user_id = opp.get("user_id")
        rep_name = user_map.get(user_id, "Unknown")

        # Skip excluded users
        if rep_name in EXCLUDE_USERS:
            continue

        lead_id = opp.get("lead_id", "")
        value_cents = opp.get("value", 0) or 0
        value_dollars = value_cents / 100

        if rep_name not in rep_data:
            rep_data[rep_name] = {"deals": 0, "revenue": 0.0}

        # Count one deal per lead per rep
        lead_key = f"{rep_name}:{lead_id}"
        if lead_key not in seen_leads:
            rep_data[rep_name]["deals"] += 1
            seen_leads.add(lead_key)

        rep_data[rep_name]["revenue"] += value_dollars

    # Step 4: Build final structure
    total_revenue = sum(r["revenue"] for r in rep_data.values())
    total_deals = sum(r["deals"] for r in rep_data.values())

    reps = []
    for name, metrics in rep_data.items():
        quota = REP_QUOTAS.get(name, 0)
        pct = (metrics["revenue"] / quota * 100) if quota > 0 else 0
        reps.append({
            "name": name,
            "deals": metrics["deals"],
            "revenue": metrics["revenue"],
            "quota": quota,
            "pct_to_quota": round(pct, 1),
        })

    # Also add reps with quotas who have zero deals this month
    for name, quota in REP_QUOTAS.items():
        if name not in rep_data and name not in EXCLUDE_USERS:
            reps.append({
                "name": name,
                "deals": 0,
                "revenue": 0.0,
                "quota": quota,
                "pct_to_quota": 0.0,
            })

    # Sort by revenue descending
    reps.sort(key=lambda r: r["revenue"], reverse=True)

    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "month_label": now.strftime("%B %Y"),
        "total_revenue": round(total_revenue, 2),
        "total_deals": total_deals,
        "reps": reps,
    }


if __name__ == "__main__":
    data = build_dashboard_data()

    # Write to data.json in repo root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data.json")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n✅ Dashboard data written to {output_path}")
    print(f"   Month: {data['month_label']}")
    print(f"   Total Revenue: ${data['total_revenue']:,.2f}")
    print(f"   Total Deals: {data['total_deals']}")
    print(f"   Reps tracked: {len(data['reps'])}")
