#!/usr/bin/env python3
"""Refresh data/github.json from the GitHub GraphQL API.

Run daily by .github/workflows/refresh-profile.yml. Standard library only.

    GITHUB_TOKEN=... python scripts/fetch_data.py          # real data
    python scripts/fetch_data.py --sample                  # deterministic fake data for previews

Token notes
-----------
The workflow's built-in GITHUB_TOKEN can read public profile data, so the
calendar counts public contributions only. To include private contributions,
add a classic PAT with `read:user` as the repo secret PROFILE_TOKEN (the
workflow prefers it when present) and switch on "Include private contributions
on my profile" in your GitHub profile settings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "github.json"
LOGIN = os.environ.get("PROFILE_USER", "praj1t")

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    bio
    followers { totalCount }
    following { totalCount }
    repositories(privacy: PUBLIC, ownerAffiliations: [OWNER], isFork: false) { totalCount }
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository { name } } }
    topRepos: repositories(
      first: 50, privacy: PUBLIC, ownerAffiliations: [OWNER], isFork: false,
      orderBy: { field: PUSHED_AT, direction: DESC }
    ) {
      nodes {
        name
        description
        url
        stargazerCount
        forkCount
        pushedAt
        primaryLanguage { name }
        repositoryTopics(first: 6) { nodes { topic { name } } }
        languages(first: 5, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name } }
        }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def gql(token: str, query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-card-renderer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as err:
        sys.exit(f"GitHub API error {err.code}: {err.read().decode()[:400]}")
    if payload.get("errors"):
        sys.exit(f"GraphQL errors: {json.dumps(payload['errors'])[:600]}")
    return payload["data"]


def fetch_real() -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("Set GITHUB_TOKEN (or GH_TOKEN), or run with --sample.")
    user = gql(token, QUERY, {"login": LOGIN})["user"]
    if user is None:
        sys.exit(f"No GitHub user called {LOGIN!r}")

    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for week in cal["weeks"]
        for d in week["contributionDays"]
    ]
    repos = []
    for r in user["topRepos"]["nodes"]:
        repos.append(
            {
                "name": r["name"],
                "description": r["description"],
                "url": r["url"],
                "stars": r["stargazerCount"],
                "forks": r["forkCount"],
                "pushed_at": r["pushedAt"],
                "language": (r["primaryLanguage"] or {}).get("name"),
                "topics": [t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]],
                "languages": [
                    {"name": e["node"]["name"], "size": e["size"]} for e in r["languages"]["edges"]
                ],
            }
        )
    return {
        "sample": False,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": {
            "login": user["login"],
            "name": user["name"],
            "bio": user["bio"],
            "followers": user["followers"]["totalCount"],
            "following": user["following"]["totalCount"],
            "public_repos": user["repositories"]["totalCount"],
        },
        "contributions": {"total": cal["totalContributions"], "days": days},
        "pinned": [n["name"] for n in user["pinnedItems"]["nodes"] if n],
        "repos": repos,
    }


def make_sample() -> dict:
    """Deterministic placeholder data so the cards can be previewed offline.

    The numbers are invented. The first real run of the workflow replaces them.
    """
    rng = random.Random(7)
    today = dt.date(2026, 11, 11)
    # Calendar starts on the Sunday 52 weeks before this week's Sunday, like GitHub's graph.
    this_sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    start = this_sunday - dt.timedelta(weeks=52)
    days, d = [], start
    quiet_until = None
    while d <= today:
        age = (today - d).days
        count = 0
        if quiet_until and d <= quiet_until:
            count = 0
        else:
            if rng.random() < 0.04:
                quiet_until = d + dt.timedelta(days=rng.randint(4, 12))
            weekday = d.weekday() < 5
            p = (0.62 if weekday else 0.34) * (0.55 + 0.45 * (1 - age / 400))
            if rng.random() < p:
                count = max(1, int(rng.expovariate(1 / 3.6)))
                if rng.random() < 0.05:
                    count += rng.randint(8, 16)
        if age <= 8:  # a live streak at the end
            count = max(count, rng.randint(1, 6))
        days.append({"date": d.isoformat(), "count": count})
        d += dt.timedelta(days=1)

    return {
        "sample": True,
        "generated_at": "2026-11-11T05:17:00Z",
        "profile": {
            "login": LOGIN,
            "name": "Prajit Balan",
            "bio": None,
            "followers": 12,
            "following": 20,
            "public_repos": 7,
        },
        "contributions": {"total": sum(x["count"] for x in days), "days": days},
        "pinned": [],
        "repos": [
            {
                "name": "Callender",
                "description": "A real-time voice scheduling agent that checks availability, books, "
                "reschedules and cancels appointments in Google Calendar.",
                "url": f"https://github.com/{LOGIN}/Callender",
                "stars": 0,
                "forks": 0,
                "pushed_at": "2026-09-18T03:35:58Z",
                "language": "Python",
                "topics": [],
                "languages": [{"name": "Python", "size": 41000}],
            },
            {
                "name": "Student-Records-API",
                "description": "A FastAPI backend for student records on SQLite and SQLAlchemy, with an "
                "MCP endpoint so Claude Desktop can use the same operations as tools.",
                "url": f"https://github.com/{LOGIN}/Student-Records-API",
                "stars": 0,
                "forks": 0,
                "pushed_at": "2026-11-06T04:20:28Z",
                "language": "Python",
                "topics": [],
                "languages": [
                    {"name": "Python", "size": 52000},
                    {"name": "Mako", "size": 700},
                ],
            },
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", action="store_true", help="write deterministic fake data (no network)")
    args = ap.parse_args()
    data = make_sample() if args.sample else fetch_real()
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=1) + "\n")
    kind = "sample" if args.sample else "live"
    print(f"wrote {DATA_FILE.relative_to(ROOT)} ({kind}; {len(data['contributions']['days'])} days, "
          f"{len(data['repos'])} repos)")


if __name__ == "__main__":
    main()
