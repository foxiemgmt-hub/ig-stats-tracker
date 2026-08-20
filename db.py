"""SQLite storage for tracked accounts + their stat snapshots."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = "data.db"

WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# Reel-view performance tiers. "View jail" matches the term for accounts
# whose reels get suppressed to a low, flat view count regardless of content.
CATEGORY_THRESHOLDS = [
    ("view_jail", 300),
    ("low", 1000),
    ("solid", 10000),
]
# anything above the last threshold is "breaking_out"; avg_reel_views of
# None (never fetched, or no reels found) is "no_data"

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class InvalidUsername(ValueError):
    pass


def normalize_username(raw):
    username = raw.strip().lstrip("@").lower()
    if not username or not USERNAME_RE.match(username):
        raise InvalidUsername(f"'{raw}' isn't a valid Instagram username")
    return username


def categorize(avg_reel_views):
    if avg_reel_views is None:
        return "no_data"
    for name, ceiling in CATEGORY_THRESHOLDS:
        if avg_reel_views <= ceiling:
            return name
    return "breaking_out"


@contextmanager
def get_conn():
    # timeout lets concurrent writers (parallel account fetches) wait out a
    # locked db instead of erroring immediately
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY,
                added_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                followers INTEGER,
                following INTEGER,
                media_count INTEGER,
                reels_sampled INTEGER,
                avg_reel_views INTEGER,
                total_reel_views INTEGER,
                avg_likes INTEGER,
                avg_comments INTEGER,
                profile_pic_url TEXT,
                reels_json TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_username_time ON snapshots(username, fetched_at)"
        )
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
        if "profile_pic_url" not in existing_cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN profile_pic_url TEXT")
        if "reels_json" not in existing_cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN reels_json TEXT")


def add_account(raw_username):
    username = normalize_username(raw_username)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (username, added_at) VALUES (?, ?)",
            (username, datetime.utcnow().isoformat()),
        )
    return username


def remove_account(username):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE username = ?", (username,))


def list_accounts():
    with get_conn() as conn:
        rows = conn.execute("SELECT username, added_at FROM accounts ORDER BY added_at").fetchall()
        return [dict(r) for r in rows]


def insert_snapshot(result):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO snapshots
                (username, fetched_at, followers, following, media_count,
                 reels_sampled, avg_reel_views, total_reel_views, avg_likes, avg_comments,
                 profile_pic_url, reels_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["username"],
                datetime.utcnow().isoformat(),
                result.get("followers"),
                result.get("following"),
                result.get("media_count"),
                result.get("reels_sampled"),
                result.get("avg_reel_views"),
                result.get("total_reel_views"),
                result.get("avg_likes"),
                result.get("avg_comments"),
                result.get("profile_pic_url"),
                json.dumps(result.get("reels") or []),
                result.get("error"),
            ),
        )


def latest_snapshot(conn, username):
    row = conn.execute(
        "SELECT * FROM snapshots WHERE username = ? ORDER BY fetched_at DESC LIMIT 1",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def previous_snapshot(conn, username, before_id):
    """The snapshot immediately before `before_id` - used for since-last-check deltas."""
    row = conn.execute(
        "SELECT * FROM snapshots WHERE username = ? AND id < ? ORDER BY fetched_at DESC LIMIT 1",
        (username, before_id),
    ).fetchone()
    return dict(row) if row else None


def earliest_snapshot_since(conn, username, since_iso):
    row = conn.execute(
        "SELECT * FROM snapshots WHERE username = ? AND fetched_at >= ? ORDER BY fetched_at ASC LIMIT 1",
        (username, since_iso),
    ).fetchone()
    return dict(row) if row else None


def _delta(field, latest, other):
    if not latest or not other or latest["id"] == other["id"]:
        return None
    a, b = latest.get(field), other.get(field)
    if a is None or b is None:
        return None
    return a - b


def dashboard_rows():
    """One row per tracked account: latest stats, since-last-check deltas,
    and follower growth over the 24h / 7d / 30d windows."""
    now = datetime.utcnow()
    with get_conn() as conn:
        accounts = conn.execute("SELECT username, added_at FROM accounts ORDER BY added_at").fetchall()
        out = []
        for acct in accounts:
            username = acct["username"]
            latest = latest_snapshot(conn, username)
            if latest is not None:
                try:
                    latest["reels"] = json.loads(latest.get("reels_json") or "[]")
                except (TypeError, ValueError):
                    latest["reels"] = []

            if not latest:
                category = "no_data"
            elif latest["error"]:
                category = "error"
            else:
                category = categorize(latest["avg_reel_views"])

            entry = {
                "username": username,
                "added_at": acct["added_at"],
                "latest": latest,
                "category": category,
                "since_last_followers": None,
                "since_last_avg_reel_views": None,
                "delta_followers_24h": None,
                "delta_followers_7d": None,
                "delta_followers_30d": None,
            }

            if latest:
                prev = previous_snapshot(conn, username, latest["id"])
                entry["since_last_followers"] = _delta("followers", latest, prev)
                entry["since_last_avg_reel_views"] = _delta("avg_reel_views", latest, prev)
                for key, delta in WINDOWS.items():
                    earliest = earliest_snapshot_since(conn, username, (now - delta).isoformat())
                    entry[f"delta_followers_{key}"] = _delta("followers", latest, earliest)

            out.append(entry)
        return out
