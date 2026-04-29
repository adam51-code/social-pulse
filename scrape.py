#!/usr/bin/env python3
"""
Nerds Do It Better — Social Listening scraper.

Standalone script. Reads keywords.json, scrapes X via Apify, optionally
summarizes with Claude, writes data.json. No HTTP server.

Usage:
  Local:   python3 scrape.py        (reads .env)
  CI/CD:   APIFY_TOKEN=... python3 scrape.py
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data.json"
KEYWORDS_PATH = ROOT / "keywords.json"
ENV_PATH = ROOT / ".env"


def load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TWEETS_PER_KEYWORD = int(os.environ.get("TWEETS_PER_KEYWORD", "40"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))

if not APIFY_TOKEN:
    print("Missing APIFY_TOKEN (set in .env locally, or as a GitHub repo secret in CI)", file=sys.stderr)
    sys.exit(1)


# --------------------------- Apify scraping ---------------------------

APIFY_ACTOR = "apidojo~tweet-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?token={APIFY_TOKEN}"


def http_post_json(url, payload, timeout=240, headers=None):
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def scrape_keyword(keyword):
    payload = {
        "searchTerms": [keyword],
        "maxItems": TWEETS_PER_KEYWORD,
        "sort": "Latest",
        "tweetLanguage": "en",
    }
    return http_post_json(APIFY_URL, payload)


def first(d, *keys, default=None):
    for k in keys:
        if d is None:
            return default
        v = d.get(k)
        if v is not None:
            return v
    return default


def normalize(t, keyword):
    author = t.get("author") or {}
    return {
        "id": first(t, "id", "tweetId", "url"),
        "keyword": keyword,
        "url": first(t, "url", "twitterUrl"),
        "text": first(t, "text", "fullText", default=""),
        "createdAt": first(t, "createdAt", "created_at", "date"),
        "lang": t.get("lang"),
        "author": {
            "userName": first(author, "userName", "username", "screen_name"),
            "name": first(author, "name", "displayName"),
            "profilePicture": first(author, "profilePicture", "profileImageUrl"),
            "followers": first(author, "followers", "followersCount", default=0),
            "following": author.get("following") or 0,
            "description": author.get("description") or "",
            "isVerified": bool(first(author, "isVerified", "verified", default=False)),
        },
        "likes": first(t, "likeCount", "favoriteCount", default=0) or 0,
        "retweets": t.get("retweetCount") or 0,
        "replies": t.get("replyCount") or 0,
        "views": first(t, "viewCount", "views", default=0) or 0,
        "isReply": bool(t.get("isReply") or t.get("inReplyToId")),
        "isRetweet": bool(t.get("isRetweet")),
    }


# --------------------------- Company-account classifier ---------------------------

_COMPANY_NAME_TOKENS = {
    "inc", "llc", "llp", "lp", "ltd", "plc", "corp", "corporation", "co", "gmbh",
    "sas", "bv", "ag", "nv", "pty", "ab", "oy",
    "agency", "studios", "studio", "labs", "group", "solutions", "services",
    "software", "technologies", "technology", "marketing", "media", "digital",
    "consulting", "network", "platform", "tools", "hq", "official", "team",
    "systems", "ventures", "partners", "associates", "industries", "holdings",
    "enterprises",
}

_COMPANY_NAME_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _COMPANY_NAME_TOKENS) + r")\b",
    re.IGNORECASE,
)

_GLUED_SUFFIX_REGEX = re.compile(r"(?:LLP|LLC|Inc|Ltd|PLC|Corp|GmbH)$")

_COMPANY_BIO_PATTERNS = [
    re.compile(r"^\s*(we['’]?re|we are|we help|we offer|we provide|we build|we make|we create|we deliver|we partner|we work)\b", re.IGNORECASE),
    re.compile(r"^\s*(official|the official) (account|page|twitter)", re.IGNORECASE),
    re.compile(r"\b(is|are) (a|the) (leading|premier|top|world['’]?s|global|trusted|award.winning|full.service) ", re.IGNORECASE),
    re.compile(r"\b(agency|company|firm|saas|platform|startup|consultancy) (offering|providing|specializing|focused|that helps|that builds|that creates)\b", re.IGNORECASE),
    re.compile(r"\b(innovator|provider|creator|manufacturer|developer|publisher|distributor|maker)\s+of\b", re.IGNORECASE),
    re.compile(r"\bour (team|clients|customers|mission|product|platform|services)\b", re.IGNORECASE),
    re.compile(r"\b(headquartered|based) in [A-Z]", re.IGNORECASE),
    re.compile(r"\bfollow us\b", re.IGNORECASE),
    re.compile(r"\b(b2b|b2c|saas) (platform|software|company|tool)\b", re.IGNORECASE),
    re.compile(r"\b(information technology|it|software|tech|digital|marketing|advertising|consulting|design)\s+(company|agency|firm|studio|consultancy|brand)\b", re.IGNORECASE),
]


def is_company_account(author):
    """Heuristic: True if this looks like a brand/company account, not an individual."""
    name = (author.get("name") or "").strip()
    user = (author.get("userName") or "").strip()
    bio = (author.get("description") or "").strip()

    if _COMPANY_NAME_REGEX.search(name):
        if not re.search(r"\bi['’]?m\b|\bmy name is\b|\bi help\b|\bi build\b|\bi write\b", bio, re.IGNORECASE):
            return True

    if _COMPANY_NAME_REGEX.search(user):
        if not re.search(r"\bi['’]?m\b|\bmy name is\b|\bi help\b|\bi build\b", bio, re.IGNORECASE):
            return True

    if _GLUED_SUFFIX_REGEX.search(name) or _GLUED_SUFFIX_REGEX.search(user):
        return True

    for pat in _COMPANY_BIO_PATTERNS:
        if pat.search(bio):
            return True

    if name and bio:
        escaped = re.escape(name)
        if re.search(rf"^\s*{escaped}\s+is\s+(a|an|the)\b", bio, re.IGNORECASE):
            return True

    return False


def parse_iso(s):
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def filter_and_dedupe(tweets, config):
    seen = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    min_eng = config.get("minEngagement", 0)
    exclude_replies = config.get("excludeReplies", True)
    exclude_companies = config.get("excludeCompanies", True)

    out = []
    drop_company = 0
    for t in tweets:
        if not t["id"] or t["id"] in seen:
            continue
        seen.add(t["id"])
        if exclude_replies and t["isReply"]:
            continue
        ts = parse_iso(t["createdAt"])
        if ts and ts < cutoff:
            continue
        eng = (t["likes"] or 0) + (t["retweets"] or 0) + (t["replies"] or 0)
        if eng < min_eng:
            continue
        if exclude_companies and is_company_account(t["author"]):
            drop_company += 1
            continue
        out.append(t)
    return out, drop_company


# --------------------------- Claude summary ---------------------------

def summarize_with_claude(by_keyword):
    if not ANTHROPIC_API_KEY:
        return None

    sections = []
    for kw, tweets in by_keyword.items():
        if not tweets:
            sections.append(f"## {kw}\n(no tweets)")
            continue
        lines = []
        for t in tweets[:15]:
            txt = re.sub(r"\s+", " ", t["text"]).strip()[:240]
            lines.append(f"- @{t['author'].get('userName')}: {txt}")
        sections.append(f"## {kw}\n" + "\n".join(lines))
    digest = "\n\n".join(sections)

    prompt = (
        "You are a marketing intelligence analyst for Nerds Do It Better, a CRO and digital marketing agency.\n\n"
        "Below are recent tweets grouped by keyword. For each keyword, write 3-5 punchy bullets describing what people are actually talking about — themes, pain points, hot takes, tools being mentioned, debates. Be specific. Skip generic observations. If a keyword has no real signal, say so in one line.\n\n"
        "At the end, add a section '## TOP OPPORTUNITIES' with 2-3 specific content angles or conversations Nerds Do It Better could jump into today.\n\n"
        f"Tweets:\n{digest}\n\n"
        "Respond in markdown. No preamble."
    )

    try:
        result = http_post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": "claude-sonnet-4-5",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            timeout=120,
        )
        return result.get("content", [{}])[0].get("text")
    except Exception as e:
        print(f"[summary] failed: {e}", file=sys.stderr)
        return None


# --------------------------- Main ---------------------------

def main():
    config = json.loads(KEYWORDS_PATH.read_text())
    started = datetime.now(timezone.utc).isoformat()
    print(f"[scrape] starting for {len(config['keywords'])} keywords")

    by_keyword = {}
    total_raw = 0
    total_companies_filtered = 0

    for kw in config["keywords"]:
        try:
            print(f"[scrape] \"{kw}\"...", flush=True)
            raw = scrape_keyword(kw)
            total_raw += len(raw)
            normalized = [normalize(t, kw) for t in raw]
            kept, dropped_company = filter_and_dedupe(normalized, config)
            total_companies_filtered += dropped_company
            kept.sort(key=lambda t: (t["likes"] or 0) + (t["retweets"] or 0) * 2, reverse=True)
            by_keyword[kw] = kept
            print(f"[scrape]   {len(raw)} raw -> {len(kept)} kept ({dropped_company} brand accts filtered)", flush=True)
        except Exception as e:
            print(f"[scrape] failed \"{kw}\": {e}", file=sys.stderr)
            by_keyword[kw] = []

    total_kept = sum(len(v) for v in by_keyword.values())

    print("[scrape] generating summary...", flush=True)
    summary = summarize_with_claude(by_keyword)

    data = {
        "refreshedAt": started,
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "keywords": len(config["keywords"]),
            "totalRaw": total_raw,
            "totalKept": total_kept,
            "companiesFiltered": total_companies_filtered,
            "lookbackHours": LOOKBACK_HOURS,
        },
        "summary": summary,
        "byKeyword": by_keyword,
    }
    DATA_PATH.write_text(json.dumps(data, indent=2))
    print(f"[scrape] done — {total_kept} tweets across {len(config['keywords'])} keywords")


if __name__ == "__main__":
    main()
