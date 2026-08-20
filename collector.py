"""Pulls follower counts and reel view stats for Instagram accounts via the
Apify Instagram Profile Scraper actor (apify/instagram-profile-scraper).

Apify runs the actual scraping on their own infrastructure (their own proxy
pool, their own anti-block handling) - this app just calls their API and
parses the result. Replaces an earlier from-scratch approach that hit
Instagram's endpoints directly, which proved unreliable at any real volume.
"""
import os

import requests

ACTOR_RUN_URL = "https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items"


def load_lines(path):
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return []


def load_token(path="apify_token.txt"):
    env_token = os.environ.get("APIFY_TOKEN")
    if env_token:
        return env_token
    lines = load_lines(path)
    return lines[0] if lines else None


def summarize(profile):
    reel_views = []
    likes = []
    comments = []
    reels = []
    for post in profile.get("latestPosts", []):
        if post.get("type") != "Video":
            continue
        views = post.get("videoViewCount")
        like_count = post.get("likesCount", 0) or 0
        comment_count = post.get("commentsCount", 0) or 0
        if views is not None:
            reel_views.append(views)
        likes.append(like_count)
        comments.append(comment_count)
        reels.append({
            "shortcode": post.get("shortCode"),
            "thumbnail_url": post.get("displayUrl"),
            "views": views,
            "likes": like_count,
            "comments": comment_count,
            "taken_at": post.get("timestamp"),
        })

    return {
        "followers": profile.get("followersCount"),
        "following": profile.get("followsCount"),
        "media_count": profile.get("postsCount"),
        "reels_sampled": len(reel_views),
        "avg_reel_views": round(sum(reel_views) / len(reel_views)) if reel_views else None,
        "total_reel_views": sum(reel_views) if reel_views else None,
        "avg_likes": round(sum(likes) / len(likes)) if likes else None,
        "avg_comments": round(sum(comments) / len(comments)) if comments else None,
        "profile_pic_url": profile.get("profilePicUrlHD") or profile.get("profilePicUrl"),
        "reels": reels[:6],
    }


def run(accounts, proxies=None, max_workers=None, on_result=None):
    """Fetch stats for every account in one Apify actor call.

    `proxies`/`max_workers` are accepted for backwards compatibility with
    the app's call sites but unused - Apify handles its own proxying.

    Returns a list of dicts, each with an "error" key (None on success).
    """
    if not accounts:
        return []

    token = load_token()
    if not token:
        return [
            {"username": u, "error": "No Apify API token configured (apify_token.txt is missing/empty)"}
            for u in accounts
        ]

    try:
        resp = requests.post(
            ACTOR_RUN_URL,
            params={"token": token},
            json={"usernames": accounts},
            timeout=180,
        )
        resp.raise_for_status()
        profiles = resp.json()
    except Exception as exc:
        error = f"Apify request failed: {exc}"
        results = [{"username": u, "error": error} for u in accounts]
        if on_result:
            for r in results:
                on_result(r)
        return results

    by_username = {}
    for profile in profiles:
        username = (profile.get("username") or "").lower()
        if username:
            by_username[username] = profile

    results = []
    for username in accounts:
        profile = by_username.get(username.lower())
        if profile is None:
            result = {"username": username, "error": "not returned by Apify (private, deleted, or invalid username)"}
        elif profile.get("followersCount") is None:
            result = {"username": username, "error": f"Apify returned no data: {profile}"}
        else:
            result = {"username": username, "error": None, **summarize(profile)}
        results.append(result)
        if on_result:
            on_result(result)
    return results


if __name__ == "__main__":
    accounts = load_lines("accounts.txt")
    if not accounts:
        print("accounts.txt is empty - add usernames (one per line) first.")
    else:
        for r in run(accounts):
            print(r)
