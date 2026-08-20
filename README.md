# IG Stats Tracker

Local dashboard that pulls follower counts and reel view stats for a list of
Instagram accounts. Fetching goes through the **Apify Instagram Profile
Scraper** (`apify/instagram-profile-scraper`) rather than hitting Instagram
directly — Apify runs the actual scraping on their own infrastructure, which
is what makes this reliable (an earlier from-scratch approach hitting
Instagram's endpoints directly worked, but broke down under any real usage).

## Setup

```bash
cd ~/ig-stats-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Requires an Apify account (apify.com) and API token, stored in
`apify_token.txt` (one line, just the token - already set up). Apify's free
tier covers light use; beyond that it's pay-per-use (a few cents per refresh
batch), billed to whatever Apify account owns that token.

`proxies.txt` is no longer used (Apify handles its own proxying) - harmless
to leave in place, or delete it.

## Running

```bash
python app.py
```

Open http://localhost:5000.

- **Add accounts** by pasting into the box at the top — one per line, or
  comma/space separated, `@` optional. Fetched immediately so they show real
  data right away.
- **Remove** an account with the `×` next to its name.
- **Refresh now** re-fetches every tracked account in a single Apify call.
  Takes roughly 30-40 seconds regardless of list size (one batched request,
  not one per account).
- **Table / Cards toggle** at the top right — Table is the dense row-per-
  account view; Cards is a per-account layout that also shows recent reel
  thumbnails with view counts, clickable straight to the reel.
- **Status badges** categorize each account by its average reel views:
  - 🔒 **View Jail** — ≤ 300 avg views (suppressed/new-account ceiling)
  - 📉 **Low** — ≤ 1,000
  - **Solid** — ≤ 10,000
  - 🚀 **Breaking Out** — > 10,000
  - ⚠️ **Fetch Error** — last fetch failed
  - **Not fetched** — added but never successfully fetched yet

  Thresholds are constants (`CATEGORY_THRESHOLDS`) at the top of `db.py` —
  edit them directly if 300/1,000/10,000 don't match what you consider
  "stuck."
- The **filter chips** narrow the view to one status; both views sort
  worst-performing first by default (View Jail → Low → Solid → Breaking Out
  → Error → Not fetched, then by avg reel views within a tier) so struggling
  accounts surface at a glance.
- **Δ 24h / 7d / 30d** columns show follower growth since the earliest
  snapshot in each window; the small green/red number under Followers and
  Avg Reel Views shows the change since the *previous* check specifically.

Data is stored in `data.db` (SQLite) in this folder — every refresh adds a
new snapshot per account rather than overwriting, which is what makes the
trend windows and deltas work. Nothing is deleted automatically; removing an
account from the dashboard just stops tracking it going forward, its history
stays in `data.db`.

## Notes / limitations

- Costs money per refresh once past Apify's free tier - keep an eye on your
  Apify billing if you're refreshing a large list frequently.
- If an account comes back with no data (private, deleted, typo'd username),
  it shows as a "Fetch Error" badge rather than crashing the rest of the
  batch.
- Runs locally only, no auth. Don't expose port 5000 to the internet as-is.
