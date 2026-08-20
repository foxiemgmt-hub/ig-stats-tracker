"""Local dashboard for the IG stats tracker. Run with: python app.py"""
import functools
import hmac
import os
import re
import time
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request

import collector
import db

app = Flask(__name__)
db.init_db()


def _load_credentials():
    creds = {}
    for line in collector.load_lines("credentials.txt"):
        if "=" in line:
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()
    for key in ("owner_password", "viewer_password"):
        env_value = os.environ.get(key.upper())
        if env_value:
            creds[key] = env_value
    return creds


_CREDENTIALS = _load_credentials()


def _role_for(username, password):
    if not password:
        return None
    owner_pw = _CREDENTIALS.get("owner_password")
    viewer_pw = _CREDENTIALS.get("viewer_password")
    if owner_pw and hmac.compare_digest(password, owner_pw):
        return "owner"
    if viewer_pw and hmac.compare_digest(password, viewer_pw):
        return "viewer"
    return None


def _unauthorized():
    return Response(
        "Login required", 401,
        {"WWW-Authenticate": 'Basic realm="IG Stats Tracker"'},
    )


def require_role(min_role):
    """min_role: 'viewer' (any valid login) or 'owner' (owner only)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not _CREDENTIALS.get("owner_password"):
                # no credentials configured - local-only use, no auth needed
                return fn(*args, **kwargs)
            auth = request.authorization
            role = _role_for(auth.username, auth.password) if auth else None
            if role is None:
                return _unauthorized()
            if min_role == "owner" and role != "owner":
                return jsonify({"error": "Read-only access - ask the owner to make this change"}), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator

# Instagram's CDN sends Cross-Origin-Resource-Policy: same-origin on image
# responses, which browsers block from a different origin (this app runs on
# 127.0.0.1). Proxying same-origin sidesteps that. Small in-memory cache since
# each fetch reissues a freshly signed URL anyway.
_IMG_ALLOWED_HOSTS = re.compile(r"(^|\.)(fbcdn\.net|cdninstagram\.com|instagram\.com)$")
_IMG_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_img_cache = {}
_IMG_CACHE_TTL = 3600

# One-time convenience: if accounts.txt has real usernames and the db is
# empty, seed the db from it so existing setups aren't dropped.
_existing = db.list_accounts()
if not _existing:
    for _u in collector.load_lines("accounts.txt"):
        try:
            db.add_account(_u)
        except db.InvalidUsername:
            pass


@app.route("/")
@require_role("viewer")
def index():
    return render_template("index.html")


@app.route("/api/img")
@require_role("viewer")
def api_img():
    url = request.args.get("url", "")
    host = urlparse(url).hostname or ""
    if not _IMG_ALLOWED_HOSTS.search(host):
        return "", 400

    cached = _img_cache.get(url)
    if cached and cached[2] > time.time():
        return Response(cached[0], mimetype=cached[1])

    try:
        resp = requests.get(url, headers=_IMG_HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception:
        return "", 404

    content_type = resp.headers.get("Content-Type", "image/jpeg")
    _img_cache[url] = (resp.content, content_type, time.time() + _IMG_CACHE_TTL)
    return Response(resp.content, mimetype=content_type)


@app.route("/api/stats")
@require_role("viewer")
def api_stats():
    return jsonify({"accounts": db.dashboard_rows()})


@app.route("/api/accounts", methods=["POST"])
@require_role("owner")
def api_add_account():
    body = request.get_json(silent=True) or {}
    raw = body.get("username", "")
    source = body.get("source") or None
    try:
        username = db.add_account(raw, source=source)
    except db.InvalidUsername as exc:
        return jsonify({"error": str(exc)}), 400

    # fetch immediately so the account doesn't sit at "no data" until the
    # next full refresh
    result = collector.run([username])[0]
    db.insert_snapshot(result)
    return jsonify({"username": username, "fetch_error": result.get("error")})


@app.route("/api/accounts/bulk", methods=["POST"])
@require_role("owner")
def api_bulk_add_accounts():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    source = body.get("source") or None
    tokens = [t for t in re.split(r"[\s,]+", text) if t]

    added = []
    invalid = []
    seen = set()
    for token in tokens:
        try:
            username = db.add_account(token, source=source)
        except db.InvalidUsername:
            invalid.append(token)
            continue
        if username in seen:
            continue
        seen.add(username)
        added.append(username)

    results = collector.run(added, on_result=db.insert_snapshot) if added else []
    failed = [r["username"] for r in results if r.get("error")]

    return jsonify({"added": added, "invalid": invalid, "failed": failed, "skipped": []})


@app.route("/api/accounts/<username>", methods=["DELETE"])
@require_role("owner")
def api_remove_account(username):
    db.remove_account(username)
    return jsonify({"removed": username})


@app.route("/api/accounts/<username>/source", methods=["PUT"])
@require_role("owner")
def api_set_account_source(username):
    source = (request.get_json(silent=True) or {}).get("source") or None
    try:
        db.set_source(username, source)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"username": username, "source": source})


@app.route("/api/refresh", methods=["POST"])
@require_role("owner")
def api_refresh():
    accounts = [a["username"] for a in db.list_accounts()]
    if not accounts:
        return jsonify({"error": "No accounts yet - add one above first"}), 400

    results = collector.run(accounts, on_result=db.insert_snapshot)
    failed = [r["username"] for r in results if r.get("error")]
    return jsonify({"refreshed": len(results), "failed": failed, "skipped": []})


if __name__ == "__main__":
    # debug=True enables Werkzeug's interactive debugger, which gives anyone
    # who triggers an unhandled error a live Python console - fine for pure
    # localhost dev, a real remote-code-execution hole the moment this is
    # reachable from outside (tunnel, hosting, etc). Stays off.
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
