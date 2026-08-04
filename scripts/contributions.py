#!/usr/bin/env python3
"""
Fetch GitHub contribution data from the public contributions endpoint and
generate data/contributions.json.

Improvements over the original version:
- Automatic retries for transient GitHub failures
- Persistent HTTP session
- Better timeout/error handling
- Robust tooltip parsing
- More reliable current streak logic
- GitHub Actions friendly
"""

import datetime
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USERNAME = os.environ.get("GH_PROFILE_USER", "devp1866")

URL = f"https://github.com/users/{USERNAME}/contributions"

OUT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "contributions.json",
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/138.0 Safari/537.36 "
    "GitHubContributionFetcher/2.0"
)


def create_session():
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=["GET"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)

    session = requests.Session()

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
    )

    return session


SESSION = create_session()


def fetch_page():
    try:
        response = SESSION.get(
            URL,
            timeout=30,
            allow_redirects=True,
        )

        response.raise_for_status()

        return response.text

    except requests.RequestException as exc:
        print(
            f"Failed to download GitHub contributions page:\n{exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def parse_tooltip(text):
    """
    Examples:
        No contributions on August 3rd.
        1 contribution on July 19th.
        9 contributions on August 2nd.
    """

    if not text:
        return 0

    if "No contributions" in text:
        return 0

    m = re.search(r"(\d+)", text)

    if not m:
        return 0

    return int(m.group(1))


def fetch_days():
    html = fetch_page()

    soup = BeautifulSoup(html, "html.parser")

    cells = soup.select("td.ContributionCalendar-day")

    if not cells:
        raise RuntimeError(
            "GitHub contribution graph could not be parsed. "
            "GitHub may have changed its HTML."
        )

    tooltip_lookup = {}

    for tip in soup.find_all("tool-tip"):
        target = tip.get("for")
        if target:
            tooltip_lookup[target] = tip.get_text(strip=True)

    days = []

    for cell in cells:

        date = cell.get("data-date")

        if not date:
            continue

        tooltip = tooltip_lookup.get(cell.get("id"), "")

        count = parse_tooltip(tooltip)

        days.append(
            {
                "date": date,
                "count": count,
            }
        )

    days.sort(key=lambda x: x["date"])

    if not days:
        raise RuntimeError("No contribution days were extracted.")

    return days


def compute_current_streak(days):
    """
    Compute the current contribution streak.

    Today's zero contributions do not end the streak because the day
    may not be finished yet.
    """

    if not days:
        return 0, None, None

    today = datetime.date.today().isoformat()

    idx = len(days) - 1

    # Ignore today's unfinished day only.
    if (
        days[idx]["date"] == today
        and days[idx]["count"] == 0
    ):
        idx -= 1

    if idx < 0:
        return 0, None, None

    streak = 0
    end_index = idx

    while idx >= 0 and days[idx]["count"] > 0:
        streak += 1
        idx -= 1

    if streak == 0:
        return 0, None, None

    start_index = idx + 1

    return (
        streak,
        days[start_index]["date"],
        days[end_index]["date"],
    )


def compute_longest_streak(days):
    """
    Compute the longest consecutive streak of contribution days.
    """

    longest = 0
    current = 0

    longest_start = None
    longest_end = None

    current_start = None

    for i, day in enumerate(days):

        if day["count"] > 0:

            if current == 0:
                current_start = i

            current += 1

            if current > longest:
                longest = current
                longest_start = days[current_start]["date"]
                longest_end = day["date"]

        else:
            current = 0
            current_start = None

    return (
        longest,
        longest_start,
        longest_end,
    )


def compute_monthly_totals(days):
    """
    Aggregate contributions by month.
    """

    monthly = {}

    for day in days:

        month = day["date"][:7]

        monthly.setdefault(month, 0)

        monthly[month] += day["count"]

    return [
        {
            "month": month,
            "total": total,
        }
        for month, total in sorted(monthly.items())
    ]


def compute_best_day(days):
    """
    Return the day with the most contributions.
    """

    if not days:
        return {
            "date": None,
            "count": 0,
        }

    best = max(days, key=lambda d: d["count"])

    return {
        "date": best["date"],
        "count": best["count"],
    }
    
    
def build_data(days):
    """
    Build the final JSON structure consumed by the profile renderer.
    """

    if not days:
        raise RuntimeError("No contribution data available.")

    total_contributions = sum(day["count"] for day in days)

    active_days = sum(
        1
        for day in days
        if day["count"] > 0
    )

    average = (
        round(total_contributions / active_days, 1)
        if active_days
        else 0
    )

    current_length, current_start, current_end = (
        compute_current_streak(days)
    )

    longest_length, longest_start, longest_end = (
        compute_longest_streak(days)
    )

    best_day = compute_best_day(days)

    monthly = compute_monthly_totals(days)

    # contribution_levels = {
    #     "0": 0,
    #     "1_4": 0,
    #     "5_9": 0,
    #     "10_19": 0,
    #     "20_plus": 0,
    # }

    # for day in days:

    #     count = day["count"]

    #     if count == 0:
    #         contribution_levels["0"] += 1

    #     elif count <= 4:
    #         contribution_levels["1_4"] += 1

    #     elif count <= 9:
    #         contribution_levels["5_9"] += 1

    #     elif count <= 19:
    #         contribution_levels["10_19"] += 1

    #     else:
    #         contribution_levels["20_plus"] += 1

    return {
        "username": USERNAME,

        "generated_at": (
            datetime.datetime.utcnow()
            .replace(microsecond=0)
            .isoformat()
            + "Z"
        ),

        "range": {
            "start": days[0]["date"],
            "end": days[-1]["date"],
        },

        "total_contributions": total_contributions,

        "active_days": active_days,

        "inactive_days": len(days) - active_days,

        "avg_per_active_day": average,

        "current_streak": {
            "length": current_length,
            "start": current_start,
            "end": current_end,
        },

        "longest_streak": {
            "length": longest_length,
            "start": longest_start,
            "end": longest_end,
        },

        "best_day": best_day,

        "monthly": monthly,

        # "distribution": contribution_levels,

        "days": days,
    }
    
    
def write_json(data):
    """
    Safely write JSON using an atomic replace.

    This prevents partially-written files if the workflow
    is interrupted while writing.
    """

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    tmp_path = OUT_PATH + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(
            data,
            fp,
            indent=2,
            ensure_ascii=False,
        )
        fp.write("\n")

    os.replace(tmp_path, OUT_PATH)


def print_summary(data):
    """
    Print a concise summary for GitHub Actions logs.
    """

    print("=" * 60)
    print(f"GitHub Contributions for {data['username']}")
    print("=" * 60)
    print(f"Total Contributions : {data['total_contributions']}")
    print(f"Active Days         : {data['active_days']}")
    print(f"Current Streak      : {data['current_streak']['length']}")
    print(f"Longest Streak      : {data['longest_streak']['length']}")
    print(
        f"Best Day            : "
        f"{data['best_day']['date']} "
        f"({data['best_day']['count']})"
    )
    print(f"Output              : {OUT_PATH}")
    print("=" * 60)


def main():
    start = time.time()

    try:
        days = fetch_days()

        data = build_data(days)

        write_json(data)

        print_summary(data)

        elapsed = round(time.time() - start, 2)

        print(f"Completed in {elapsed}s")

        return 0

    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    except Exception as exc:
        print("\nERROR:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    
    
if __name__ == "__main__":
    sys.exit(main())