# Sales Dashboard (GitHub Pages)

Automated MTD sales dashboard that pulls Closed/Won opportunity data from Close CRM and displays it on a GitHub Pages site.

## What It Shows

- **MTD Revenue** — total value of Closed/Won opportunities this month
- **MTD Deals Closed** — count of unique Closed/Won deals this month
- **Per-Rep Breakdown** — deals, revenue, and % to quota for each rep

## Quick Setup (5 minutes)

### 1. Create the GitHub repo

Create a **new repository** on GitHub (public or private with GitHub Pro).
Push this project:

```bash
git init
git add .
git commit -m "Initial dashboard setup"
git branch -M main
git remote add origin https://github.com/YOUR_ORG/sales-dashboard.git
git push -u origin main
```

### 2. Add the Close API key as a secret

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `CLOSE_API_KEY`
4. Value: your Close API key
5. Click **Add secret**

> ⚠️ **Never commit the API key to the repo.** The GitHub Actions workflow reads it from the secret at runtime.

### 3. Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Branch: `main`, Folder: `/ (root)`
4. Click **Save**

Your dashboard will be live at: `https://YOUR_ORG.github.io/sales-dashboard/`

### 4. Trigger the first data pull

1. Go to the repo → **Actions** tab
2. Click **Update Sales Dashboard** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Wait ~30 seconds for it to complete
5. Visit your GitHub Pages URL — you should see live data

## How It Works

```
GitHub Actions (hourly cron)
    ↓
scripts/fetch_data.py (Python)
    ↓  Fetches from Close API:
    ↓  GET /opportunity/?status_id=Closed/Won&date_won__gte=YYYY-MM-01
    ↓
data.json (committed to repo)
    ↓
index.html (reads data.json, renders dashboard)
    ↓
GitHub Pages (serves index.html)
```

**Schedule**: Runs every hour Mon–Fri, 7 AM – 5 PM PST. Also runs on manual trigger.

## Files

| File | Purpose |
|------|---------|
| `index.html` | Dashboard UI (loads `data.json` client-side) |
| `data.json` | Latest dashboard data (auto-updated by GitHub Actions) |
| `scripts/fetch_data.py` | Fetches data from Close API, writes `data.json` |
| `.github/workflows/update-dashboard.yml` | Hourly automation schedule |

## Customization

### Change rep quotas
Edit the `REP_QUOTAS` dict in `scripts/fetch_data.py`.

### Change schedule
Edit the `cron` lines in `.github/workflows/update-dashboard.yml`.

### Add/remove excluded users
Edit the `EXCLUDE_USERS` set in `scripts/fetch_data.py`.

## Troubleshooting

**Dashboard shows zeros**: Run the workflow manually (Actions → Run workflow) and check the logs.

**API errors**: Verify your `CLOSE_API_KEY` secret is set correctly. The key must have read access to opportunities and users.

**Page not loading**: Make sure GitHub Pages is enabled and pointing to the `main` branch root.

**Stale data**: The page auto-refreshes every 5 minutes. You can also hard-refresh (Ctrl+Shift+R).
