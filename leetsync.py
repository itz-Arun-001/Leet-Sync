"""
leetsync.py — automatic LeetCode -> GitHub topic-organized sync

Polls your LeetCode account for newly Accepted submissions, downloads the
code, looks up each problem's topic tags, writes the solution into the
right topic folder(s) inside a local git repo, and commits + pushes to
GitHub. Run it manually after solving, or schedule it (Windows Task
Scheduler) to run every N minutes for near-automatic syncing, similar to
tools like LeetSync.

------------------------------------------------------------------------
ONE-TIME SETUP
------------------------------------------------------------------------
1. Copy config.example.json to config.json (same folder as this script)
   and fill in the values. NEVER commit config.json to git — it contains
   your LeetCode session cookie, which is as sensitive as a password.

   How to get LEETCODE_SESSION and CSRF_TOKEN:
     a. Log into leetcode.com in Chrome/Edge/Firefox.
     b. Open DevTools (F12) -> Application tab (Chrome/Edge) or
        Storage tab (Firefox) -> Cookies -> https://leetcode.com
     c. Find the cookie named "LEETCODE_SESSION" and copy its Value.
     d. Find the cookie named "csrftoken" and copy its Value.
     e. Paste both into config.json.

   NOTE: LEETCODE_SESSION expires periodically (days to weeks). When
   this script starts failing to fetch code, just repeat the steps
   above to get a fresh value.

2. Make sure repo_dir in config.json points to a folder that is already
   a git repository connected to your GitHub remote (git remote -v
   should show your GitHub URL). If not, run once inside that folder:
     git init
     git remote add origin https://github.com/yourusername/yourrepo.git
     git branch -M main

3. Add config.json and .leetsync_state.json to your repo's .gitignore
   so secrets and sync-state never get committed:
     echo config.json >> .gitignore
     echo .leetsync_state.json >> .gitignore

------------------------------------------------------------------------
RUNNING IT
------------------------------------------------------------------------
    python leetsync.py

For near-automatic behavior, schedule it with Windows Task Scheduler to
run every 15-30 minutes. It's safe to run repeatedly — already-synced
problems are skipped (tracked in .leetsync_state.json).

To backfill README.md (description + example test cases + an Approach
section) into problem folders that were synced before this feature
existed, run once:
    python leetsync.py --backfill
This only fills in folders missing a README.md; it never touches
solution.<ext> files and is safe to re-run.

If you've solved problems on LeetCode that were never synced by this
script at all (e.g. you have 300+ solved on LeetCode but only ~20 ever
showed up here, since normal runs only look at your last ~20 accepted
submissions), run a one-time full import instead:
    python leetsync.py --import-history
This walks your ENTIRE submission history (not just recent ones), and
for every accepted submission not already synced, writes both
solution.<ext> and README.md, then commits + pushes. It can take several
minutes for a large history since it makes ~2 LeetCode API calls per
problem; progress is saved every 15 problems, so if it's interrupted
(network blip, rate limit) just rerun the same command and it resumes
instead of starting over.
------------------------------------------------------------------------
README.md AUTO-UPDATE
------------------------------------------------------------------------
After every sync that adds or changes files, leetsync.py now rebuilds the
repo's README.md (via generate_readme.py, which must sit in the same
folder as this script) BEFORE committing, so the README is pushed in the
same commit as the new solutions. To rebuild just the README on demand:
    python leetsync.py --readme
To turn the automatic rebuild off, add  "update_readme": false  to
config.json.
------------------------------------------------------------------------
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

GRAPHQL_URL = "https://leetcode.com/graphql"

RECENT_AC_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""

# Paginated submission history (what leetcode.com/submissions/ itself uses).
# Unlike recentAcSubmissionList (hard-capped around your last ~20 AC's),
# this walks your ENTIRE submission history via offset/lastKey paging, so
# it's what makes it possible to import all 300+ problems, not just recent
# ones. It's an authenticated, undocumented endpoint (same caveat as the
# submissions/code endpoint below) -- LeetCode could change its shape.
SUBMISSION_LIST_QUERY = """
query submissionList($offset: Int!, $limit: Int!, $lastKey: String) {
  submissionList(offset: $offset, limit: $limit, lastKey: $lastKey) {
    lastKey
    hasNext
    submissions {
      id
      title
      titleSlug
      statusDisplay
      timestamp
    }
  }
}
"""

TAGS_QUERY = """
query questionDetail($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    difficulty
    content
    exampleTestcases
    topicTags {
      name
    }
  }
}
"""

# Maps LeetCode's lang_name values to file extensions
# Maps LeetCode's lang_name values to file extensions. LeetCode's
# submissions endpoint has been observed returning both slug-style names
# ("python3", "csharp") and display-style names ("C++", "C#", "Go") for
# lang_name depending on the submission, so both forms are covered here.
LANG_EXT = {
    "python3": "py", "python": "py", "java": "java", "cpp": "cpp",
    "c++": "cpp", "c": "c", "csharp": "cs", "c#": "cs",
    "javascript": "js", "typescript": "ts",
    "golang": "go", "go": "go", "ruby": "rb", "swift": "swift",
    "kotlin": "kt", "rust": "rs", "php": "php", "scala": "scala",
    "mysql": "sql", "postgresql": "sql", "mssql": "sql", "oraclesql": "sql",
    "erlang": "erl", "racket": "rkt", "elixir": "ex",
}

# Comment-line prefix per extension, used to write a human-readable
# separator between multiple accepted submissions saved to the same file.
COMMENT_PREFIX = {
    "py": "#", "rb": "#", "ex": "#",
    "java": "//", "cpp": "//", "c": "//", "cs": "//", "js": "//",
    "ts": "//", "go": "//", "swift": "//", "kt": "//", "rs": "//",
    "php": "//", "scala": "//",
    "sql": "--",
    "erl": "%",
    "rkt": ";;",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: config.json not found at {CONFIG_PATH}")
        print("Copy config.example.json to config.json and fill it in first.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state(state_path):
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            state = {}
    else:
        state = {}

    # Old state files only tracked slugs. Keep that list around (informational
    # only) but switch the actual dedup key to submission id, so re-solving a
    # previously-synced problem is no longer silently skipped.
    state.setdefault("synced_slugs", [])
    state.setdefault("synced_ids", [])
    return state


def save_state(state_path, state):
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        print(f"! warning: could not save state file: {e}")


def graphql_request(query, variables, cookies=None, csrf_token=None):
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://leetcode.com",
        "Referer": "https://leetcode.com/",
    }
    if cookies:
        headers["Cookie"] = cookies
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_recent_accepted(username, limit=20):
    data = graphql_request(RECENT_AC_QUERY, {"username": username, "limit": limit})
    items = data.get("data", {}).get("recentAcSubmissionList") or []
    return items


def fetch_all_accepted_submissions(session_cookie, csrf_token, page_size=20, max_pages=500):
    """
    Walk the FULL authenticated submission history (not just the last ~20
    accepted) via submissionList's offset/lastKey pagination, and return
    every Accepted one as {id, title, titleSlug, timestamp} -- same shape
    fetch_recent_accepted returns, so it can feed the same processing loop
    in main(). Stops when the API says there's no next page, or after
    max_pages as a safety cap (500 pages * 20 = 10,000 submissions).
    """
    cookies = f"LEETCODE_SESSION={session_cookie}; csrftoken={csrf_token}"
    accepted = []
    offset = 0
    last_key = None
    for page in range(max_pages):
        data = graphql_request(
            SUBMISSION_LIST_QUERY,
            {"offset": offset, "limit": page_size, "lastKey": last_key},
            cookies=cookies, csrf_token=csrf_token,
        )
        block = (data.get("data") or {}).get("submissionList") or {}
        subs = block.get("submissions") or []
        if not subs:
            break
        for s in subs:
            if s.get("statusDisplay") == "Accepted":
                accepted.append({
                    "id": s.get("id"), "title": s.get("title"),
                    "titleSlug": s.get("titleSlug"), "timestamp": s.get("timestamp"),
                })
        print(f"  ...scanned {offset + len(subs)} submissions, {len(accepted)} accepted so far", end="\r")
        if not block.get("hasNext"):
            break
        offset += page_size
        last_key = block.get("lastKey")
        time.sleep(0.3)
    print()
    return accepted


def fetch_question_details(title_slug):
    """
    Fetch everything needed to write both the topic-tag list and a
    one-time problem statement file: frontend id, title, difficulty,
    HTML description ("content"), example test cases, and topic tags.
    Returns None if the question can't be found.
    """
    data = graphql_request(TAGS_QUERY, {"titleSlug": title_slug})
    q = data.get("data", {}).get("question")
    if not q:
        return None
    return {
        "frontend_id": q.get("questionFrontendId"),
        "title": q.get("title"),
        "difficulty": q.get("difficulty"),
        "content_html": q.get("content") or "",
        "example_testcases": q.get("exampleTestcases") or "",
        "tags": [t["name"] for t in q.get("topicTags", [])],
    }


def html_to_text(html):
    """
    Very small HTML->text converter for LeetCode's question "content"
    field. LeetCode's descriptions only use a limited set of tags, so
    this doesn't need to be a full HTML parser -- just enough to turn
    <p>/<li>/<pre>/<code>/<b>/<sup>/<img> etc. into readable plain text
    without pulling in an extra dependency.
    """
    if not html:
        return ""

    text = html

    # Block-level tags -> line breaks
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</p>', '\n\n', text)
    text = re.sub(r'(?i)<p[^>]*>', '', text)
    text = re.sub(r'(?i)</li>', '\n', text)
    text = re.sub(r'(?i)<li[^>]*>', '  - ', text)
    text = re.sub(r'(?i)</?(ul|ol)[^>]*>', '\n', text)
    text = re.sub(r'(?i)</pre>', '\n', text)
    text = re.sub(r'(?i)<pre[^>]*>', '\n', text)

    # Superscript exponents: 10<sup>4</sup> -> 10^4
    text = re.sub(r'(?i)<sup[^>]*>', '^', text)
    text = re.sub(r'(?i)</sup>', '', text)

    # Strip all remaining tags (b, strong, i, em, code, img, div, span...)
    text = re.sub(r'<[^>]+>', '', text)

    # Decode the handful of HTML entities LeetCode actually uses
    entities = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&apos;": "'", "&minus;": "-",
        "&times;": "x", "&le;": "<=", "&ge;": ">=", "&ne;": "!=",
    }
    for ent, repl in entities.items():
        text = text.replace(ent, repl)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)

    # Collapse excess blank lines / trailing whitespace per line
    lines = [ln.rstrip() for ln in text.split('\n')]
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_submission_code(title_slug, session_cookie, csrf_token, target_id=None):
    """
    Fetch an Accepted submission's code for a problem using the
    authenticated (undocumented but widely used) submissions endpoint.

    If target_id is given, return the code for that specific submission
    (matched against the dump's own "id" field) so repeat solves of the
    same problem pull the right version rather than always the newest.
    Falls back to the most recent Accepted submission if target_id isn't
    found in the dump (it only holds recent history) or wasn't provided.
    """
    url = f"https://leetcode.com/api/submissions/{title_slug}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Cookie": f"LEETCODE_SESSION={session_cookie}; csrftoken={csrf_token}",
        "X-CSRFToken": csrf_token,
        "Referer": f"https://leetcode.com/problems/{title_slug}/",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    submissions = data.get("submissions_dump", [])

    if target_id is not None:
        for sub in submissions:
            if (
                sub.get("status_display") == "Accepted"
                and str(sub.get("id")) == str(target_id)
            ):
                return sub.get("code", ""), sub.get("lang_name", "txt")

    for sub in submissions:
        if sub.get("status_display") == "Accepted":
            return sub.get("code", ""), sub.get("lang_name", "txt")
    return None, None


def safe_folder_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def write_solution(repo_dir, topic, frontend_id, title_slug, code, lang_name, timestamp=None):
    """
    Write (or append) a submission's code to the topic's solution file.

    If the file already exists, the new code is only appended when it isn't
    word-for-word identical to something already in the file — so re-running
    on an unchanged accepted submission, or one whose code exactly matches
    an earlier saved version, doesn't create a duplicate block. Returns
    (file_path, appended: bool) where appended is False if nothing changed.
    """
    ext = LANG_EXT.get(lang_name, "txt")
    comment = COMMENT_PREFIX.get(ext, "#")
    topic_dir = os.path.join(repo_dir, safe_folder_name(topic))
    problem_dir = os.path.join(topic_dir, f"{frontend_id}-{title_slug}")
    os.makedirs(problem_dir, exist_ok=True)
    file_path = os.path.join(problem_dir, f"solution.{ext}")

    code = code.rstrip("\n")

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if code.strip() and code.strip() in existing:
            # Word-for-word identical to a version already saved here.
            return file_path, False
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) if timestamp else "resync"
        header = f"\n\n{comment} ---- submission {when} ----\n\n"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(header + code + "\n")
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code + "\n")

    return file_path, True


def write_problem_statement(repo_dir, topic, frontend_id, title, title_slug,
                             difficulty, content_html, example_testcases, tags=None):
    """
    Write README.md with the problem statement (title, difficulty,
    description, example test cases, and an Approach section) into the
    topic's problem folder. Written ONCE per folder -- if README.md
    already exists it's left alone, since the statement itself never
    changes between resyncs. Returns the file path, or None if it
    already existed.
    """
    topic_dir = os.path.join(repo_dir, safe_folder_name(topic))
    problem_dir = os.path.join(topic_dir, f"{frontend_id}-{title_slug}")
    os.makedirs(problem_dir, exist_ok=True)
    file_path = os.path.join(problem_dir, "README.md")

    if os.path.exists(file_path):
        return None

    description = html_to_text(content_html)
    lines = [
        f"# {frontend_id}. {title}",
        "",
        f"**Difficulty:** {difficulty or 'Unknown'}",
        f"**Link:** https://leetcode.com/problems/{title_slug}/",
        "",
        "## Description",
        "",
        description or "_(no description available)_",
    ]
    if example_testcases and example_testcases.strip():
        lines += [
            "",
            "## Example Test Cases (raw)",
            "",
            "```",
            example_testcases.strip(),
            "```",
        ]

    tag_hint = ", ".join(tags) if tags else "N/A"
    lines += [
        "",
        "## Approach",
        "",
        f"_Tags: {tag_hint}_",
        "",
        "_(Add your approach notes here -- LeetCode's public API doesn't",
        "expose editorial/approach write-ups, so this is left for you to",
        "fill in.)_",
    ]

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return file_path


PROBLEM_DIR_RE = re.compile(r'^(\d+)-(.+)$')


def backfill_problem_statements(repo_dir):
    """
    Walk repo_dir for already-synced problem folders (topic/<id>-<slug>)
    that don't yet have a README.md, and create one for each by fetching
    the description fresh from LeetCode. Doesn't touch solution.<ext>
    files or resync code -- purely fills in missing problem statements
    for problems synced before README support existed.
    """
    if not os.path.isdir(repo_dir):
        print(f"ERROR: repo_dir does not exist: {repo_dir}")
        sys.exit(1)

    # Collect (topic, frontend_id, slug) for every problem folder missing
    # a README.md, deduping by slug so a problem filed under multiple
    # topics is only queried once but written into every topic folder.
    missing = []  # list of (topic, frontend_id, slug)
    for topic in sorted(os.listdir(repo_dir)):
        topic_dir = os.path.join(repo_dir, topic)
        if not os.path.isdir(topic_dir) or topic.startswith('.') or topic == '.git':
            continue
        for entry in sorted(os.listdir(topic_dir)):
            problem_dir = os.path.join(topic_dir, entry)
            if not os.path.isdir(problem_dir):
                continue
            m = PROBLEM_DIR_RE.match(entry)
            if not m:
                continue
            if os.path.exists(os.path.join(problem_dir, "README.md")):
                continue
            frontend_id, slug = m.group(1), m.group(2)
            missing.append((topic, frontend_id, slug))

    if not missing:
        print("No problem folders are missing a README.md. Nothing to backfill.")
        return []

    slugs = sorted(set(slug for _, _, slug in missing))
    print(f"Found {len(missing)} folder(s) across {len(slugs)} problem(s) missing a README.md.\n")

    details_by_slug = {}
    written = []
    for slug in slugs:
        print(f"Fetching description: {slug}")
        try:
            details = fetch_question_details(slug)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  ! could not fetch details: {e}")
            continue
        if not details:
            print(f"  ! question not found, skipping")
            continue
        details_by_slug[slug] = details
        time.sleep(0.5)

    for topic, frontend_id, slug in missing:
        details = details_by_slug.get(slug)
        if not details:
            continue
        path = write_problem_statement(
            repo_dir, topic, details["frontend_id"] or frontend_id,
            details["title"] or slug, slug, details["difficulty"],
            details["content_html"], details["example_testcases"],
            tags=details["tags"],
        )
        if path:
            print(f"  -> {path}")
            written.append(path)

    return written


def run_git(args, cwd):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git_sync(repo_dir, commit_message):
    code, out, err = run_git(["add", "."], repo_dir)
    if code != 0:
        print(f"! git add failed: {err}")
        return False

    code, out, err = run_git(["diff", "--cached", "--quiet"], repo_dir)
    if code == 0:
        print("No changes to commit.")
        return True

    code, out, err = run_git(["commit", "-m", commit_message], repo_dir)
    if code != 0:
        print(f"! git commit failed: {err}")
        return False
    print(out)

    code, out, err = run_git(["push"], repo_dir)
    if code != 0:
        print(f"! git push failed: {err}")
        return False
    print("Pushed to GitHub.")
    return True


def refresh_readme(cfg, repo_dir):
    """Rebuild README.md in repo_dir (stats + topic table + charts).
    Never lets a README problem break the sync itself."""
    if cfg.get("update_readme", True) is False:
        return False
    try:
        import generate_readme
        return bool(generate_readme.build_readme(repo_dir, cfg))
    except ImportError:
        print("! generate_readme.py not found next to leetsync.py -- README not updated.")
    except Exception as e:
        print(f"! README refresh failed (sync itself is fine): {e}")
    return False


def main():
    cfg = load_config()
    username = cfg["leetcode_username"]
    session_cookie = cfg["leetcode_session"]
    csrf_token = cfg["csrf_token"]
    repo_dir = cfg["repo_dir"]
    poll_limit = cfg.get("poll_limit", 20)

    if not os.path.isdir(repo_dir):
        print(f"ERROR: repo_dir does not exist: {repo_dir}")
        sys.exit(1)

    if "--readme" in sys.argv:
        print(f"Rebuilding README.md in: {repo_dir}\n")
        if refresh_readme(cfg, repo_dir):
            git_sync(repo_dir, "Update README")
        return

    if "--backfill" in sys.argv:
        print(f"Backfilling missing README.md files under: {repo_dir}\n")
        written = backfill_problem_statements(repo_dir)
        if written:
            refresh_readme(cfg, repo_dir)
            print(f"\nCommitting and pushing {len(written)} new README.md file(s)...")
            git_sync(repo_dir, f"Backfill problem statements for {len(written)} solution folder(s)")
        return

    state_path = os.path.join(SCRIPT_DIR, ".leetsync_state.json")
    state = load_state(state_path)
    synced_ids = set(state.get("synced_ids", []))

    import_history = "--import-history" in sys.argv

    if import_history:
        print(f"Scanning FULL submission history for '{username}' (this can take a few minutes)...")
    else:
        print(f"Checking recent accepted submissions for '{username}'...")
    try:
        if import_history:
            recent = fetch_all_accepted_submissions(session_cookie, csrf_token)
        else:
            recent = fetch_recent_accepted(username, poll_limit)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"ERROR: could not reach LeetCode: {e}")
        sys.exit(1)

    # Dedup by submission id, not problem slug, so re-solving a problem
    # you've already synced is picked up as a new submission to process.
    new_items = [item for item in recent if str(item["id"]) not in synced_ids]

    if not new_items:
        print("No new accepted submissions since last sync.")
        return

    print(f"Found {len(new_items)} new accepted submission(s).\n")

    newly_synced_ids = []
    newly_synced_slugs = []
    for item in new_items:
        sub_id = str(item["id"])
        slug = item["titleSlug"]
        title = item["title"]
        ts = item.get("timestamp")
        try:
            ts = int(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None
        print(f"Processing: {title} ({slug}) [submission {sub_id}]")

        try:
            details = fetch_question_details(slug)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  ! could not fetch question details: {e}")
            continue
        if not details or not details["tags"]:
            print(f"  ! no tags found, skipping")
            continue
        frontend_id = details["frontend_id"]
        tags = details["tags"]

        try:
            code, lang_name = fetch_submission_code(slug, session_cookie, csrf_token, target_id=sub_id)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  ! could not fetch code (session cookie may have expired): {e}")
            continue

        if not code:
            print(f"  ! no accepted code found, skipping")
            continue

        lang_key = lang_name.lower().replace(" ", "") if lang_name else "txt"
        if lang_key not in LANG_EXT:
            print(f"  ! unrecognized language '{lang_name}' -> saving as .txt (tell Claude this value to add it)")
        any_written = False
        for topic in tags:
            readme_path = write_problem_statement(
                repo_dir, topic, frontend_id, title, slug,
                details["difficulty"], details["content_html"],
                details["example_testcases"], tags=tags,
            )
            if readme_path:
                print(f"  -> {readme_path}")
                any_written = True

            path, appended = write_solution(repo_dir, topic, frontend_id, slug, code, lang_key, timestamp=ts)
            if appended:
                print(f"  -> {path}")
                any_written = True
            else:
                print(f"  -> {path} (identical to a saved version, skipped)")

        # Mark this submission id as handled either way, so we don't keep
        # re-fetching it every run once we've looked at it once.
        newly_synced_ids.append(sub_id)
        if any_written:
            newly_synced_slugs.append(slug)
        time.sleep(0.5)

        # Save progress every 15 items so a long --import-history run
        # (hundreds of API calls) doesn't lose everything if it's
        # interrupted or hits a rate limit partway through -- just rerun
        # the same command and it'll pick up where it left off.
        if len(newly_synced_ids) % 15 == 0:
            synced_ids.update(newly_synced_ids)
            state["synced_ids"] = sorted(synced_ids)
            save_state(state_path, state)

    if newly_synced_ids:
        synced_ids.update(newly_synced_ids)
        state["synced_ids"] = sorted(synced_ids)
        save_state(state_path, state)

    if newly_synced_slugs:
        commit_msg = f"Sync {len(newly_synced_slugs)} submission(s): " + ", ".join(newly_synced_slugs[:5])
        if len(newly_synced_slugs) > 5:
            commit_msg += f" (+{len(newly_synced_slugs) - 5} more)"

        print("\nUpdating README.md...")
        refresh_readme(cfg, repo_dir)

        print("\nCommitting and pushing to GitHub...")
        git_sync(repo_dir, commit_msg)
    elif newly_synced_ids:
        print("\nNo file changes to commit (all submissions matched saved code).")
    else:
        print("\nNothing new was successfully synced.")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        print("\nSCRIPT CRASHED — full error below:")
        traceback.print_exc()
    finally:
        if sys.stdin.isatty():
            try:
                input("\nPress Enter to close this window...")
            except EOFError:
                pass