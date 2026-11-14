"""
generate_readme.py -- rebuild the README.md at the root of your LeetCode
solutions repo (same layout every time, numbers always fresh).

What it does
------------
  * Scans the repo's topic folders and counts problems per topic, languages
    used, and unique problems (a problem filed under several topics is
    counted once in the totals, and once per topic in the topic table).
  * Pulls your live LeetCode profile numbers (solved / easy / medium / hard,
    attempting, contest rating, rank, badges, submissions, active days,
    max streak) from LeetCode's GraphQL API.
  * Writes README.md with badges, profile snapshot, difficulty chart,
    top-20 topic chart, full topic table, and the how-to-navigate sections.

If LeetCode can't be reached (offline, rate limit, API change), it falls
back to the last numbers it fetched successfully (saved in
.readme_profile_cache.json next to this script, NOT inside your repo), so
the README never breaks -- it just prints a warning.

leetsync.py calls this automatically after every sync. You can also run it
by hand:

    python generate_readme.py                 # uses repo_dir from config.json
    python generate_readme.py "F:\\leet_solutions"
    python generate_readme.py --offline       # skip the LeetCode fetch
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
CACHE_PATH = os.path.join(SCRIPT_DIR, ".readme_profile_cache.json")
GRAPHQL_URL = "https://leetcode.com/graphql"

# Folders at the repo root that are not topic folders.
IGNORE_DIRS = {".git", ".github", "__pycache__", "node_modules"}
UNCATEGORIZED = "_uncategorized"

PROBLEM_DIR_RE = re.compile(r"^(\d+)-(.+)$")

# Last known profile numbers, used only if nothing has ever been fetched.
DEFAULT_PROFILE = {
    "solved": {"All": 502, "Easy": 150, "Medium": 314, "Hard": 38},
    "totals": {"All": 4073, "Easy": 969, "Medium": 2124, "Hard": 980},
    "attempting": 9,
    "contest_rating": 1470,
    "contests_attended": 2,
    "rank": 214181,
    "badge_count": 2,
    "recent_badge": "100 Days Badge 2026",
    "submissions_past_year": 2184,
    "active_days": 181,
    "max_streak": 102,
}

TOPIC_EMOJI = {
    "0-1 Knapsack": "🎒",
    "Algorithm X": "❌",
    "Array": "🧮",
    "Backtracking": "🔙",
    "Binary Indexed Tree": "🌳",
    "Binary Search": "🔍",
    "Binary Search Tree": "🌴",
    "Binary Tree": "🌳",
    "Bipartite Graph": "🕸️",
    "Bit Manipulation": "🧩",
    "Bitmask": "🎭",
    "Bracket Sequences": "🔣",
    "Brainteaser": "🧠",
    "Breadth-First Search": "🌊",
    "Bucket Sort": "🪣",
    "Combinatorics": "🎰",
    "Complete Knapsack": "🎒",
    "Counting": "🔢",
    "Counting Sort": "🧮",
    "Dancing Links": "💃",
    "Data Stream": "🌐",
    "Depth-First Search": "🕳️",
    "Design": "🛠️",
    "Directed Acyclic Graph": "➡️",
    "Divide and Conquer": "⚔️",
    "Doubly-Linked List": "🔗",
    "Dynamic Programming": "🧠",
    "Enumeration": "📋",
    "Eulerian Circuit": "🔄",
    "Eulerian Path": "🛣️",
    "Flow Network": "🚰",
    "Game Theory": "🎲",
    "Geometry": "📐",
    "Graph Coloring": "🎨",
    "Graph Theory": "🕸️",
    "Greedy": "🪙",
    "Hamiltonian Path": "🛤️",
    "Hash Function": "🔑",
    "Hash Table": "🔑",
    "Heap (Priority Queue)": "🚏",
    "Hungarian Algorithm": "🇭🇺",
    "Impartial Game": "🎲",
    "Interactive": "🕹️",
    "Iterator": "🔄",
    "Knapsack Problem": "🎒",
    "Linked List": "🔗",
    "Longest Common Subsequence": "🧵",
    "Matching (Graph)": "🤝",
    "Math": "➗",
    "Matrix": "🔲",
    "Memoization": "🗂️",
    "Merge Sort": "🔀",
    "Minimax": "🎯",
    "Minimum-Cost Flow": "🚰",
    "Monotonic Queue": "🚏",
    "Monotonic Stack": "📈",
    "Nim Game": "🎲",
    "Number Theory": "🔢",
    "Perfect Matching": "🤝",
    "Prefix Sum": "➕",
    "Primality Test": "🔢",
    "Queue": "🚏",
    "Quickselect": "⚡",
    "Recursion": "🔁",
    "Rolling Hash": "📌",
    "Segment Tree": "🌳",
    "Semi-Eulerian Graph": "🌉",
    "Simulation": "🎛️",
    "Sliding Window": "🪟",
    "Sorting": "📶",
    "Stack": "📚",
    "String": "🔤",
    "Successive Shortest Path Algorithm": "🚰",
    "Topological Sort": "📑",
    "Tree": "🌲",
    "Trie": "🌲",
    "Two Pointers": "👉",
    "Union-Find": "🕸️",
    "Zero-Sum Game": "⚖️",
    "_uncategorized": "❓"
}

CHART_COLORS = [
    "#4C9AFF", "#79E2F2", "#57D9A3", "#FFC400", "#FF991F",
    "#FF5630", "#6554C0", "#00B8D9", "#36B37E", "#8777D9",
]

# extension -> (display name, emoji, shields color, shields logo)
LANGS = {
    "cpp": ("C++", "💻", "00599C", "cplusplus"),
    "java": ("Java", "☕", "ED8B00", "openjdk"),
    "py": ("Python", "🐍", "3776AB", "python"),
    "c": ("C", "💻", "A8B9CC", "c"),
    "cs": ("C#", "💻", "512BD4", "csharp"),
    "js": ("JavaScript", "💻", "F7DF1E", "javascript"),
    "ts": ("TypeScript", "💻", "3178C6", "typescript"),
    "go": ("Go", "💻", "00ADD8", "go"),
    "rs": ("Rust", "💻", "000000", "rust"),
    "kt": ("Kotlin", "💻", "7F52FF", "kotlin"),
    "swift": ("Swift", "💻", "F05138", "swift"),
    "rb": ("Ruby", "💻", "CC342D", "ruby"),
    "php": ("PHP", "💻", "777BB4", "php"),
    "scala": ("Scala", "💻", "DC322F", "scala"),
    "sql": ("SQL", "🗄️", "4479A1", ""),
}


# --------------------------------------------------------------------------
# Repo scan
# --------------------------------------------------------------------------
def scan_repo(repo_dir):
    """Return (topic_counts, problems) where problems maps folder name
    ('1-two-sum') -> set of language extensions found for it."""
    topic_counts = {}
    problems = {}
    for topic in sorted(os.listdir(repo_dir)):
        tdir = os.path.join(repo_dir, topic)
        if (not os.path.isdir(tdir) or topic.startswith(".")
                or topic in IGNORE_DIRS):
            continue
        n = 0
        for entry in os.listdir(tdir):
            pdir = os.path.join(tdir, entry)
            if not os.path.isdir(pdir) or not PROBLEM_DIR_RE.match(entry):
                continue
            n += 1
            langs = problems.setdefault(entry, set())
            for fn in os.listdir(pdir):
                if fn.lower() == "readme.md":
                    continue
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if ext in LANGS:
                    langs.add(ext)
        if n:
            topic_counts[topic] = n
    return topic_counts, problems


# --------------------------------------------------------------------------
# LeetCode profile
# --------------------------------------------------------------------------
def _gql(query, variables, cfg):
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Origin": "https://leetcode.com",
        "Referer": "https://leetcode.com/",
    }
    sess, csrf = cfg.get("leetcode_session"), cfg.get("csrf_token")
    if sess and csrf:
        headers["Cookie"] = f"LEETCODE_SESSION={sess}; csrftoken={csrf}"
        headers["X-CSRFToken"] = csrf
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("data") or {}


def _max_streak(calendar_json):
    """Longest run of consecutive active days in LeetCode's submissionCalendar
    ({"<unix day start>": count, ...})."""
    try:
        cal = json.loads(calendar_json) if isinstance(calendar_json, str) else (calendar_json or {})
    except ValueError:
        return None, None, None
    days = sorted({int(ts) // 86400 for ts, cnt in cal.items() if int(cnt) > 0})
    if not days:
        return 0, 0, 0
    best = run = 1
    for a, b in zip(days, days[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    subs = sum(int(c) for c in cal.values())
    return best, len(days), subs


def fetch_profile(username, cfg):
    """Fetch what we can. Every query is independent: one failing (LeetCode
    changes its API now and then) only drops that part of the profile."""
    out = {}
    problems = []

    def attempt(label, fn):
        try:
            fn()
        except Exception as e:  # network, HTTP, shape changes...
            problems.append(f"{label}: {e}")

    def solved():
        d = _gql("""query($u:String!){
            allQuestionsCount{difficulty count}
            matchedUser(username:$u){ submitStatsGlobal{ acSubmissionNum{difficulty count} } }
        }""", {"u": username}, cfg)
        tot = {x["difficulty"]: x["count"] for x in d.get("allQuestionsCount") or []}
        mine = {x["difficulty"]: x["count"]
                for x in ((d.get("matchedUser") or {}).get("submitStatsGlobal") or {}).get("acSubmissionNum") or []}
        if mine.get("All"):
            out["solved"] = mine
        if tot.get("All"):
            out["totals"] = tot

    def rank_and_calendar():
        d = _gql("""query($u:String!){
            matchedUser(username:$u){
                profile{ranking}
                userCalendar{ totalActiveDays submissionCalendar }
            }}""", {"u": username}, cfg)
        mu = d.get("matchedUser") or {}
        rank = (mu.get("profile") or {}).get("ranking")
        if rank:
            out["rank"] = rank
        cal = mu.get("userCalendar") or {}
        if cal.get("submissionCalendar"):
            streak, active, subs = _max_streak(cal["submissionCalendar"])
            if streak is not None:
                out["max_streak"] = streak
                out["active_days"] = cal.get("totalActiveDays") or active
                out["submissions_past_year"] = subs

    def contest():
        d = _gql("""query($u:String!){
            userContestRanking(username:$u){attendedContestsCount rating}
        }""", {"u": username}, cfg)
        c = d.get("userContestRanking")
        if c:
            out["contest_rating"] = int(round(c["rating"]))
            out["contests_attended"] = c["attendedContestsCount"]

    def badges():
        d = _gql("""query($u:String!){
            matchedUser(username:$u){ badges{ id displayName creationDate } }
        }""", {"u": username}, cfg)
        b = (d.get("matchedUser") or {}).get("badges") or []
        out["badge_count"] = len(b)
        if b:
            b = sorted(b, key=lambda x: str(x.get("creationDate") or ""))
            out["recent_badge"] = b[-1].get("displayName")

    def attempting():
        d = _gql("""query($u:String!){
            userProfileUserQuestionProgressV2(userSlug:$u){
                numFailedQuestions{count difficulty}
            }}""", {"u": username}, cfg)
        p = d.get("userProfileUserQuestionProgressV2") or {}
        failed = p.get("numFailedQuestions")
        if failed is not None:
            out["attempting"] = sum(int(x.get("count") or 0) for x in failed)

    attempt("solved counts", solved)
    attempt("rank/calendar", rank_and_calendar)
    attempt("contest", contest)
    attempt("badges", badges)
    attempt("attempting", attempting)
    return out, problems


def load_profile(username, cfg, offline=False):
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            profile.update(json.load(f))
    except (OSError, ValueError):
        pass
    if offline:
        print("(offline) using cached/default profile numbers.")
        return profile
    fresh, problems = fetch_profile(username, cfg)
    for p in problems:
        print(f"  ! profile fetch issue -> {p}")
    if fresh:
        profile.update(fresh)
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
        except OSError:
            pass
        if problems:
            print("  ! some profile fields kept their previous values.")
    else:
        print("  ! could not reach LeetCode -- README uses the last saved profile numbers.")
    return profile


# --------------------------------------------------------------------------
# README building
# --------------------------------------------------------------------------
def _q(s):
    return urllib.parse.quote(str(s), safe="")


def badge(label, message, color, logo=None, alt=None):
    msg = str(message).replace("-", "--").replace("_", "__")
    url = f"https://img.shields.io/badge/{_q(label)}-{_q(msg)}-{color}"
    if logo:
        url += f"?logo={logo}&logoColor=white"
    return f"![{alt or label}]({url})"


def chart_url(cfg_dict, w, h):
    return f"https://quickchart.io/chart?w={w}&h={h}&c={_q(json.dumps(cfg_dict))}"


def fmt(n):
    return f"{int(n):,}"


def folder_link(name):
    return urllib.parse.quote(name, safe="-_.~")


def build_readme(repo_dir, cfg=None, offline=False):
    cfg = cfg or {}
    username = cfg.get("leetcode_username", "Arun_001_AU")
    topic_counts, problems = scan_repo(repo_dir)
    if not topic_counts:
        print(f"No topic folders with problems found in {repo_dir}; README not written.")
        return None
    prof = load_profile(username, cfg, offline=offline)

    today = datetime.date.today().isoformat()
    unique = len(problems)
    n_topics = len(topic_counts)
    S, T = prof["solved"], prof["totals"]

    # language stats (unique problems per language)
    lang_counts = {}
    for exts in problems.values():
        for e in exts:
            lang_counts[e] = lang_counts.get(e, 0) + 1
    langs = sorted(lang_counts.items(), key=lambda x: (-x[1], LANGS[x[0]][0]))
    multi = sum(1 for e in problems.values() if len(e) > 1)

    items = sorted(topic_counts.items(),
                   key=lambda x: (-x[1], x[0].lower().lstrip("_")))
    top = [(t, c) for t, c in items if t != UNCATEGORIZED][:20]
    mx = items[0][1]

    L = []
    add = L.append

    # ---- header + badges -------------------------------------------------
    add("# 🧩 LeetCode Solutions\n")
    add(f"Solved LeetCode problems for **[{username}](https://leetcode.com/u/{username}/)**, "
        "organized by topic/pattern with per-problem write-ups, test cases, and working code.\n")
    add(badge("Solved", f"{S['All']}/{T['All']}", "brightgreen", alt="Solutions"))
    add(badge("Easy", f"{S['Easy']}/{T['Easy']}", "success"))
    add(badge("Medium", f"{S['Medium']}/{T['Medium']}", "yellow"))
    add(badge("Hard", f"{S['Hard']}/{T['Hard']}", "red"))
    for ext, cnt in langs[:4]:
        name, _, color, logo = LANGS[ext]
        add(badge(name, f"{cnt} solved", color, logo=logo or None))
    add(badge("Contest Rating", prof["contest_rating"], "blue"))
    add(badge("Max Streak", f"{prof['max_streak']} days", "orange", alt="Streak"))
    add(badge("Last Updated", today, "lightgrey"))
    add("")
    att = f" ({prof['attempting']} currently attempting)" if prof.get("attempting") else ""
    add(f"> **{fmt(S['All'])} / {fmt(T['All'])}** problems solved{att} · "
        f"{n_topics} topic folders in this repo · profile rank **{fmt(prof['rank'])}** · "
        f"contest rating **{fmt(prof['contest_rating'])}**.\n")
    add("---\n")

    # ---- TOC ---------------------------------------------------------------
    add("## 📖 Table of Contents\n")
    for label, anchor in [
        ("Profile Snapshot", "#-profile-snapshot"),
        ("Difficulty Split", "#%EF%B8%8F-difficulty-split"),
        ("Top Topics", "#-top-topics"),
        ("Topics at a Glance", "#-topics-at-a-glance"),
        ("Repository Structure", "#-repository-structure"),
        ("Anatomy of a Problem Folder", "#-anatomy-of-a-problem-folder"),
        ("How to Navigate", "#-how-to-navigate"),
        ("Notes", "#-notes"),
    ]:
        add(f"- [{label}]({anchor})")
    add("\n---\n")

    # ---- profile snapshot ------------------------------------------------
    rows = [
        ("🧠 Problems solved", f"**{fmt(S['All'])}** / {fmt(T['All'])}" +
         (f" ({prof['attempting']} attempting)" if prof.get("attempting") else "")),
        ("🟢 Easy", f"**{fmt(S['Easy'])}** / {fmt(T['Easy'])}"),
        ("🟡 Medium", f"**{fmt(S['Medium'])}** / {fmt(T['Medium'])}"),
        ("🔴 Hard", f"**{fmt(S['Hard'])}** / {fmt(T['Hard'])}"),
    ]
    for ext, cnt in langs:
        name, emoji, _, _ = LANGS[ext]
        rows.append((f"{emoji} {name}", f"**{cnt}** problems"))
    if len(langs) >= 2:
        rows.append(("🔁 Solved in both languages" if len(langs) == 2
                     else "🔁 Solved in 2+ languages", f"**{multi}** problems"))
    rows += [
        ("🏆 Contest rating",
         f"**{fmt(prof['contest_rating'])}** ({prof['contests_attended']} contests attended)"),
        ("🌐 Site rank", f"**{fmt(prof['rank'])}**"),
        ("🎖️ Badges", f"**{prof['badge_count']}**" +
         (f" — most recent: _{prof['recent_badge']}_" if prof.get("recent_badge") else "")),
        ("🔥 Activity (past year)",
         f"**{fmt(prof['submissions_past_year'])}** submissions · "
         f"**{prof['active_days']}** active days · **{prof['max_streak']}**-day max streak"),
    ]
    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows)
    add("## 👤 Profile Snapshot\n")
    add(f"| {'Metric'.ljust(w1)} | {'Value'.ljust(w2)} |")
    add(f"| {'-' * w1} | {'-' * w2} |")
    for a, b in rows:
        add(f"| {a.ljust(w1)} | {b.ljust(w2)} |")
    add("\n---\n")

    # ---- difficulty chart -------------------------------------------------
    diff = {
        "type": "doughnut",
        "data": {
            "labels": [f"{k} ({S[k]}/{T[k]})" for k in ("Easy", "Medium", "Hard")],
            "datasets": [{"data": [S["Easy"], S["Medium"], S["Hard"]],
                          "backgroundColor": ["#36B37E", "#FFC400", "#FF5630"]}],
        },
        "options": {"plugins": {
            "title": {"display": True, "text": f"Difficulty Split ({S['All']} Solved)"},
            "legend": {"position": "bottom"}}},
    }
    add("## ⚖️ Difficulty Split\n")
    add('<p align="center">')
    add(f'  <img src="{chart_url(diff, 420, 320)}" alt="Difficulty split chart" />')
    add("</p>\n")
    add("---\n")

    # ---- top topics chart ------------------------------------------------
    topc = {
        "type": "bar",
        "data": {
            "labels": [t for t, _ in top],
            "datasets": [{"label": "Problems Solved", "data": [c for _, c in top],
                          "backgroundColor": [CHART_COLORS[i % len(CHART_COLORS)]
                                              for i in range(len(top))]}],
        },
        "options": {
            "plugins": {"legend": {"display": False},
                        "title": {"display": True, "text": "Top 20 Topics by Problems Solved"}},
            "scales": {"xAxes": [{"ticks": {"autoSkip": False, "fontSize": 9}}]},
        },
    }
    add("## 📊 Top Topics\n")
    add("Top 20 topics by problems solved (counted from this repo's topic folders):\n")
    add(f"![Topic chart]({chart_url(topc, 900, 500)})\n")
    add("---\n")

    # ---- topic table -------------------------------------------------------
    add("## 🗂 Topics at a Glance\n")
    add("<details>")
    add(f"<summary><strong>Click to expand all {n_topics} topic folders in this repo</strong></summary>\n")
    add("Counts are taken from this repo's own topic folders (the LeetCode profile card truncates "
        "its skill list). A problem tagged with several topics is counted under each.\n")
    trows = []
    for t, c in items:
        n = max(1, round(c / mx * 20))
        label = "Uncategorized" if t == UNCATEGORIZED else t
        emoji = TOPIC_EMOJI.get(t, "📌")
        trows.append((f"{emoji} [{label}]({folder_link(t)})", f"**{c}**",
                      f"`{'█' * n}{'░' * (20 - n)}`"))
    tw = max(len(r[0]) for r in trows)
    tw = max(tw, len("Topic"))
    add(f"| {'Topic'.ljust(tw)} | Solved  | Progress               |")
    add(f"| {'-' * tw} | ------- | ---------------------- |")
    for a, b, c in trows:
        add(f"| {a.ljust(tw)} | {b.ljust(7)} | {c} |")
    add("\n</details>\n")
    add("---\n")

    # ---- static sections ---------------------------------------------------
    add(STATIC_STRUCTURE)
    add("---\n")

    # ---- notes ---------------------------------------------------------------
    add("## 📝 Notes\n")
    add(f"- The **{fmt(S['All'])} / {fmt(T['All'])}** headline number and the difficulty split come "
        "straight from the LeetCode profile card, so they're the ground truth. The topic table and "
        "chart are counted from this repo's folders.")
    if unique > S["All"]:
        add(f"- This repo's own folder count ({unique} unique problems) runs a little higher than "
            f"{S['All']}. That's expected: a problem gets its own folder under **every** topic tag it "
            "carries (cross-listing), and a handful of older problems were re-numbered by LeetCode over "
            "time, so the same problem occasionally exists here under two different problem IDs.")
    else:
        add(f"- This repo has {unique} unique problem folders. A problem gets its own folder under "
            "**every** topic tag it carries (cross-listing), so topic counts add up to more than that.")
    add("- `_uncategorized` holds problems that haven't been assigned a topic tag yet.")
    add("- Charts are rendered live via [QuickChart](https://quickchart.io/) — if they don't load, "
        "GitHub may be rate-limiting the embed; refresh or view the image URL directly.")
    add("- Contributions/PRs aren't expected (this is a personal solutions log), but feel free to "
        "fork it for your own practice tracker.\n")
    add("---\n")
    add('<p align="center"><i>Happy grinding 🚀</i></p>')

    path = os.path.join(repo_dir, "README.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print(f"README.md written: {fmt(S['All'])} solved, {unique} problem folders, {n_topics} topics.")
    return path


STATIC_STRUCTURE = """## 🏗 Repository Structure

Solutions are organized **by topic/pattern first**, not by difficulty or problem number, so a single problem can (and often does) live under several folders if it touches several patterns:

```
leetcode_solutions/
├── Array/
│   ├── 1-two-sum/
│   ├── 15-3sum/
│   └── ...
├── Hash Table/
│   ├── 1-two-sum/          ← same problem, different tag
│   └── ...
├── Dynamic Programming/
├── Binary Search/
├── Graph Theory/
├── _uncategorized/         ← recently added problems awaiting a tag
└── README.md
```

Folder names match LeetCode's own topic tags where possible (e.g. `Sliding Window`, `Union-Find`, `Monotonic Stack`), so you can jump straight to a pattern you want to practice.

---

## 🧬 Anatomy of a Problem Folder

Every problem folder — e.g. [`Array/1-two-sum`](Array/1-two-sum) — follows the same layout:

```
1-two-sum/
├── README.md                                          ← problem statement, constraints, tags
├── solution.java                                       ← cleanest/latest accepted Java solution
├── 2026-07-01 22.45.54 - Accepted - runtime 3ms....cpp  ← timestamped submission w/ runtime & memory
├── 2026-07-01 22.54.58 - Accepted - runtime 2ms....java
└── ...
```

- **`README.md`** — the problem statement, difficulty, a link to LeetCode, sample test cases, and an `Approach` section for notes.
- **Timestamped `.cpp` / `.java` files** — real accepted submissions, named `<date> - Accepted - runtime <x>ms - memory <y>MB`, so you can see how a solution evolved (brute force → optimized) and compare runtime/memory across attempts.
- **`solution.java` / loose `.cpp` files** — a clean, de-duplicated copy of the final approach where present.
- Some folders also include a raw test-case `.txt` file exported directly from LeetCode.

---

## 🧭 How to Navigate

- **Browsing by pattern?** Start from the [Topics at a Glance](#-topics-at-a-glance) table and click into a folder.
- **Looking for a specific problem?** Every folder is named `<problem-number>-<slug>` (e.g. `76-minimum-window-substring`), matching LeetCode's own URL slug — use your editor's fuzzy file search (`Ctrl/Cmd + P`) with the problem number or name.
- **Comparing solutions?** Open a problem's folder directly — timestamps in the filenames let you trace the progression from a first brute-force pass to the final optimized version.
- **Want just the working code?** Prefer the highest-runtime-ranked / most recent timestamp, or `solution.java` where available.
"""


# --------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv
    cfg = load_config()
    repo_dir = args[0] if args else cfg.get("repo_dir")
    if not repo_dir or not os.path.isdir(repo_dir):
        print(f"ERROR: repo_dir does not exist: {repo_dir}")
        sys.exit(1)
    build_readme(repo_dir, cfg, offline=offline)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        print("\nSCRIPT CRASHED — full error below:")
        traceback.print_exc()
    finally:
        if sys.stdin and sys.stdin.isatty():
            try:
                input("\nPress Enter to close this window...")
            except EOFError:
                pass
