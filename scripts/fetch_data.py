#!/usr/bin/env python3
"""
Fetch sales data from Close CRM and generate data.json for the GitHub Pages dashboard.

Data collected:
  1. Closed/Won opportunities (MTD + today) -> revenue & deal counts per rep
  2. Leads with "First Call Booked Date" in current month -> meetings booked per rep
  3. Leads with "First Call Show Up (Opp)" = "Yes" -> meetings shown per rep
  4. Close rate = deals closed / meetings booked

Excluded from meeting counts:
  - Leads in "Canceled (by Lead)" status
  - Leads in "Outside the US" status
"""

import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from base64 import b64encode
from calendar import monthrange

# --- Configuration ---

CLOSE_API_KEY = os.environ.get("CLOSE_API_KEY", "")
BASE_URL = "https://api.close.com/api/v1"

PIPELINE_ID = "pipe_78hyBUVS7IKikGEmstObu1"
CLOSED_WON_STATUS_ID = "stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03"

# Lead statuses to EXCLUDE from meeting counts (rep can't control these)
EXCLUDED_LEAD_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",  # Canceled (by Lead)
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB",  # Outside the US
}

# Custom field IDs and display names (lead object)
CF_FIRST_CALL_BOOKED_ID   = "cf_JsJZIVh7QDcFQBXr4cTRBxf1AkREpLdsKiZB4AEJ8Xh"
CF_FIRST_CALL_BOOKED_NAME = "First Call Booked Date"

CF_FIRST_CALL_SHOW_ID     = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_FIRST_CALL_SHOW_NAME   = "First Call Show Up (Opp)"

CF_LEAD_OWNER_ID           = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_LEAD_OWNER_NAME         = "Lead Owner"

TEAM_QUOTA = 906_000

REP_QUOTAS = {
    "Christian Hartwell": 100_000,
    "Lyle Hubbard": 100_000,
    "Ategeka Musinguzi": 100_000,
    "Scott Seymour": 100_000,
    "Eric Piccione": 100_000,
    "Jordan Humphrey": 100_000,
    "Jason Aaron": 100_000,
    "Robin Perkins": 100_000,
    "William Chase": 100_000,
    "Ryan Jones": 100_000,
    "John Kirk": 100_000,
    "Jake Skinner": 75_000,
    "Vince Bartolini": 50_000,
    "Elvis Ellis": 50_000,
    "Chris Wanke": 50_000,
    "Andrea Shoop": 50_000,
}

EXCLUDE_USERS = {"Mallory Kent", "Unknown", "Ahmad Bukhari", "Stephen Olivas", "Julia Scaroni"}

# Users who only appear on dashboard if they have closed deals that month
# Their meeting data is N/A'd out — only show on Revenue Closed and Opps Closed
DEALS_ONLY_USERS = {"Kristin Nelson", "Joe Dysert"}

# Managers don't have quotas but shouldn't show "Ramping"
MANAGER_USERS = {"Joe Dysert"}


# --- API helpers ---

def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    if params:
        url = f"{url}?{urlencode(params)}"

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
        print(f"API error {e.code} for {url}: {body}", file=sys.stderr)
        raise


def api_post(endpoint, body):
    url = f"{BASE_URL}{endpoint}"
    auth = b64encode(f"{CLOSE_API_KEY}:".encode()).decode()
    data = json.dumps(body).encode()
    req = Request(url, data=data, method="POST", headers={
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        print(f"API POST error {e.code} for {url}: {body_text}", file=sys.stderr)
        raise


def fetch_org_users():
    data = api_get("/user/")
    users = {}
    for u in data.get("data", []):
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        full = f"{first} {last}".strip()
        users[u["id"]] = full
    return users


# --- Opportunity data ---

def fetch_closed_won_opportunities(year, month):
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

    return [o for o in all_opps if o.get("pipeline_id") == PIPELINE_ID]


# --- Meeting data using lead search ---

def get_custom_value(custom_dict, field_id, field_name):
    """Try multiple key formats to get a custom field value from a lead."""
    val = custom_dict.get(field_name)
    if val is not None:
        return val
    val = custom_dict.get(field_id)
    if val is not None:
        return val
    val = custom_dict.get(f"custom.{field_id}")
    if val is not None:
        return val
    return ""


def resolve_owner_to_name(owner_raw, user_map, name_to_id):
    """Resolve a Lead Owner value (could be user_id, name, or dict) to a rep name."""
    if not owner_raw:
        return "Unknown"

    if isinstance(owner_raw, dict):
        uid = owner_raw.get("id", "")
        if uid in user_map:
            return user_map[uid]
        return owner_raw.get("name", "Unknown")

    owner_str = str(owner_raw).strip()

    if owner_str in user_map:
        return user_map[owner_str]

    if owner_str in name_to_id:
        return owner_str

    for rep_name in REP_QUOTAS:
        if owner_str == rep_name:
            return rep_name

    return owner_str if owner_str else "Unknown"


def fetch_leads_with_calls_booked(year, month, today_str, user_map, name_to_id):
    """Fetch leads with First Call Booked Date in the given month, up to today.
    
    Excludes leads in Canceled (by Lead) or Outside the US status.
    """
    date_gte = f"{year}-{month:02d}-01"
    date_lte = today_str

    query_str = (
        f'"First Call Booked Date" >= "{date_gte}" '
        f'"First Call Booked Date" <= "{date_lte}"'
    )

    all_leads = []
    skip = 0
    limit = 200

    while True:
        params = {
            "query": query_str,
            "_skip": str(skip),
            "_limit": str(limit),
        }
        data = api_get("/lead/", params)
        leads = data.get("data", [])
        all_leads.extend(leads)
        if not data.get("has_more", False):
            break
        skip += limit

    print(f"  Raw leads returned: {len(all_leads)}")

    rep_booked = {}
    rep_shown = {}
    excluded_count = 0

    for lead in all_leads:
        # --- EXCLUDE leads in Canceled or Outside US status ---
        lead_status_id = lead.get("status_id", "")
        if lead_status_id in EXCLUDED_LEAD_STATUSES:
            excluded_count += 1
            continue

        custom = lead.get("custom", {})

        # Merge any top-level custom.cf_xxx keys
        merged = {}
        merged.update(custom)
        for k, v in lead.items():
            if k.startswith("custom."):
                merged[k] = v
                merged[k.replace("custom.", "")] = v

        owner_raw = get_custom_value(merged, CF_LEAD_OWNER_ID, CF_LEAD_OWNER_NAME)
        show_up = get_custom_value(merged, CF_FIRST_CALL_SHOW_ID, CF_FIRST_CALL_SHOW_NAME)

        rep_name = resolve_owner_to_name(owner_raw, user_map, name_to_id)

        if rep_name in EXCLUDE_USERS:
            continue

        rep_booked[rep_name] = rep_booked.get(rep_name, 0) + 1
        if str(show_up).strip().lower() == "yes":
            rep_shown[rep_name] = rep_shown.get(rep_name, 0) + 1

    print(f"  Excluded {excluded_count} leads (Canceled/Outside US)")
    print(f"  Counting {len(all_leads) - excluded_count} leads after exclusions")

    return rep_booked, rep_shown


# --- Working days ---

def count_working_days(year, month, up_to_day=None):
    _, last_day = monthrange(year, month)
    end_day = min(up_to_day, last_day) if up_to_day else last_day
    count = 0
    for d in range(1, end_day + 1):
        if date(year, month, d).weekday() < 5:
            count += 1
    return count


# --- Main ---

def build_dashboard_data():
    if not CLOSE_API_KEY:
        print("ERROR: CLOSE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    # Use America/Los_Angeles for all date calculations (handles PST/PDT automatically)
    try:
        from zoneinfo import ZoneInfo
        pst = ZoneInfo("America/Los_Angeles")
    except ImportError:
        # Fallback for older Python: UTC-8
        pst = timezone(timedelta(hours=-8))
    now = now_utc.astimezone(pst)

    year, month, today_day = now.year, now.month, now.day
    today_str = now.strftime("%Y-%m-%d")
    _, last_day = monthrange(year, month)

    print(f"Fetching data for {year}-{month:02d} (day {today_day}, {now.strftime('%Z')})...")

    # Step 1: User map
    print("  Fetching org users...")
    user_map = fetch_org_users()
    name_to_id = {v: k for k, v in user_map.items()}
    print(f"  Found {len(user_map)} users.")

    # Step 2: Closed/Won opportunities
    print("  Fetching Closed/Won opportunities...")
    opps = fetch_closed_won_opportunities(year, month)
    print(f"  Found {len(opps)} Closed/Won opportunities.")

    rep_revenue = {}
    rep_deals = {}
    today_revenue = 0.0
    today_deals = 0
    seen_leads = set()

    for opp in opps:
        user_id = opp.get("user_id")
        rep_name = user_map.get(user_id, "Unknown")
        if rep_name in EXCLUDE_USERS:
            continue

        value_dollars = (opp.get("value", 0) or 0) / 100
        lead_id = opp.get("lead_id", "")
        date_won = opp.get("date_won", "")

        rep_revenue[rep_name] = rep_revenue.get(rep_name, 0) + value_dollars

        lead_key = f"{rep_name}:{lead_id}"
        if lead_key not in seen_leads:
            rep_deals[rep_name] = rep_deals.get(rep_name, 0) + 1
            seen_leads.add(lead_key)

        # Today's revenue: use date_won (the actual close date)
        if date_won == today_str:
            today_revenue += value_dollars
            today_deals += 1

    # Step 3: Meetings booked / shown (excludes Canceled & Outside US leads)
    print("  Fetching meetings booked/shown...")
    rep_booked, rep_shown = fetch_leads_with_calls_booked(year, month, today_str, user_map, name_to_id)
    print(f"  Meetings booked by {len(rep_booked)} reps, shown by {len(rep_shown)} reps.")

    # Step 4: Build per-rep data
    all_rep_names = set()
    all_rep_names.update(rep_revenue.keys())
    all_rep_names.update(rep_deals.keys())
    all_rep_names.update(rep_booked.keys())
    all_rep_names.update(REP_QUOTAS.keys())
    all_rep_names -= EXCLUDE_USERS

    reps = []
    for name in all_rep_names:
        revenue = rep_revenue.get(name, 0)
        deals = rep_deals.get(name, 0)
        quota = REP_QUOTAS.get(name, 0)

        # Deals-only users: skip entirely if they have no closed deals
        is_deals_only = name in DEALS_ONLY_USERS
        if is_deals_only and deals == 0:
            continue

        # Zero out meeting data for deals-only users
        booked = 0 if is_deals_only else rep_booked.get(name, 0)
        shown = 0 if is_deals_only else rep_shown.get(name, 0)

        pct_quota = round(revenue / quota * 100, 1) if quota > 0 else 0
        close_rate = round(deals / booked * 100, 1) if booked > 0 else 0
        show_rate = round(shown / booked * 100, 1) if booked > 0 else 0

        reps.append({
            "name": name,
            "revenue": round(revenue, 2),
            "deals": deals,
            "quota": quota,
            "pct_to_quota": pct_quota,
            "booked": booked,
            "shown": shown,
            "close_rate": close_rate,
            "show_rate": show_rate,
            "is_manager": name in MANAGER_USERS,
            "exclude_meetings": is_deals_only,
        })

    # Step 5: Team totals
    total_revenue = sum(r["revenue"] for r in reps)
    total_deals = sum(r["deals"] for r in reps)
    total_booked = sum(r["booked"] for r in reps)
    total_shown = sum(r["shown"] for r in reps)
    team_close_rate = round(total_deals / total_booked * 100, 1) if total_booked > 0 else 0
    team_show_rate = round(total_shown / total_booked * 100, 1) if total_booked > 0 else 0

    # Step 6: Time context
    working_days_total = count_working_days(year, month)
    working_days_elapsed = count_working_days(year, month, today_day)
    pct_month_passed = round(working_days_elapsed / working_days_total * 100, 1) if working_days_total > 0 else 0
    pct_team_quota = round(total_revenue / TEAM_QUOTA * 100, 1) if TEAM_QUOTA > 0 else 0

    reps.sort(key=lambda r: r["revenue"], reverse=True)

    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "month_label": now.strftime("%B %Y"),
        "day_of_month": today_day,
        "days_in_month": last_day,
        "working_days_total": working_days_total,
        "working_days_elapsed": working_days_elapsed,
        "pct_month_passed": pct_month_passed,
        "team_quota": TEAM_QUOTA,
        "pct_team_quota": pct_team_quota,
        "total_revenue": round(total_revenue, 2),
        "total_deals": total_deals,
        "total_booked": total_booked,
        "total_shown": total_shown,
        "team_close_rate": team_close_rate,
        "team_show_rate": team_show_rate,
        "today_revenue": round(today_revenue, 2),
        "today_deals": today_deals,
        "reps": reps,
    }


if __name__ == "__main__":
    data = build_dashboard_data()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data.json")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    # Auto-archive: save a snapshot for the current month
    # The last run of each month becomes the final archive
    archive_dir = os.path.join(repo_root, "archives")
    os.makedirs(archive_dir, exist_ok=True)
    from zoneinfo import ZoneInfo
    pst_now = datetime.now(ZoneInfo("America/Los_Angeles"))
    archive_name = f"data_{pst_now.strftime('%Y-%m')}.json"
    archive_path = os.path.join(archive_dir, archive_name)
    with open(archive_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"   Archive saved: archives/{archive_name}")

    # Update archives/index.json with list of all available months
    index_path = os.path.join(archive_dir, "index.json")
    archive_files = sorted(
        [f for f in os.listdir(archive_dir) if f.startswith("data_") and f.endswith(".json")],
        reverse=True,
    )
    months = []
    for af in archive_files:
        ym = af.replace("data_", "").replace(".json", "")  # e.g. "2026-02"
        months.append({"file": af, "key": ym})
    with open(index_path, "w") as f:
        json.dump({"months": months}, f, indent=2)
    print(f"   Archive index updated: {len(months)} month(s)")

    print(f"\n=== Dashboard data written to {output_path} ===")
    print(f"   Month: {data['month_label']} (day {data['day_of_month']})")
    print(f"   Total Revenue: ${data['total_revenue']:,.2f}")
    print(f"   Today Revenue: ${data['today_revenue']:,.2f}")
    print(f"   Total Deals: {data['total_deals']}")
    print(f"   Meetings Booked: {data['total_booked']} (after exclusions)")
    print(f"   Meetings Shown: {data['total_shown']}")
    print(f"   Reps tracked: {len(data['reps'])}")
