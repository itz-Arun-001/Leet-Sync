"""
add_problem_statements.py — one-time backfill of problem statements into
an EXISTING LeetCode solutions repo.

For every folder that looks like <topic>/<frontend_id>-<title-slug>/,
fetches that problem's description, difficulty, and example test cases
from LeetCode's public API and writes a README.md into it (if one isn't
already there). It never creates, renames, or touches your solution
files — purely additive, and safe to re-run (already-README'd folders
are skipped).

No login/session cookie needed — problem descriptions are public data.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------
    python add_problem_statements.py /path/to/your/repo

If you don't pass a path, it uses the current directory.
------------------------------------------------------------------------
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

GRAPHQL_URL = "https://leetcode.com/graphql"

QUESTION_QUERY = """
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

PROBLEM_DIR_RE = re.compile(r'^(\d+)-(.+)$')


def graphql_request(query, variables):
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
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_question_details(title_slug):
    data = graphql_request(QUESTION_QUERY, {"titleSlug": title_slug})
    q = (data.get("data") or {}).get("question")
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
    """Small HTML->text converter for LeetCode's question 'content' field."""
    if not html:
        return ""
    text = html
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</p>', '\n\n', text)
    text = re.sub(r'(?i)<p[^>]*>', '', text)
    text = re.sub(r'(?i)</li>', '\n', text)
    text = re.sub(r'(?i)<li[^>]*>', '  - ', text)
    text = re.sub(r'(?i)</?(ul|ol)[^>]*>', '\n', text)
    text = re.sub(r'(?i)</pre>', '\n', text)
    text = re.sub(r'(?i)<pre[^>]*>', '\n', text)
    text = re.sub(r'(?i)<sup[^>]*>', '^', text)
    text = re.sub(r'(?i)</sup>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    entities = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&apos;": "'", "&minus;": "-",
        "&times;": "x", "&le;": "<=", "&ge;": ">=", "&ne;": "!=",
    }
    for ent, repl in entities.items():
        text = text.replace(ent, repl)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    lines = [ln.rstrip() for ln in text.split('\n')]
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def write_readme(problem_dir, frontend_id, title, title_slug, difficulty,
                  content_html, example_testcases, tags):
    file_path = os.path.join(problem_dir, "README.md")
    if os.path.exists(file_path):
        return None  # already has one, don't touch it

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


def main():
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    repo_dir = os.path.abspath(repo_dir)

    if not os.path.isdir(repo_dir):
        print(f"ERROR: not a directory: {repo_dir}")
        sys.exit(1)

    # Collect (topic, frontend_id, slug) for every problem folder missing
    # a README.md, deduping by slug so a problem filed under multiple
    # topics is only fetched once but a README is written into every copy.
    missing = []
    for topic in sorted(os.listdir(repo_dir)):
        topic_dir = os.path.join(repo_dir, topic)
        if not os.path.isdir(topic_dir) or topic.startswith('.'):
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
            missing.append((topic, m.group(1), m.group(2), problem_dir))

    if not missing:
        print("Every problem folder already has a README.md. Nothing to do.")
        return

    slugs = sorted(set(slug for _, _, slug, _ in missing))
    print(f"Found {len(missing)} folder(s) across {len(slugs)} unique problem(s) missing a README.md.\n")

    details_by_slug = {}
    for i, slug in enumerate(slugs, 1):
        print(f"[{i}/{len(slugs)}] Fetching: {slug}")
        try:
            details = fetch_question_details(slug)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  ! could not fetch details: {e}")
            continue
        if not details:
            print(f"  ! question not found, skipping")
            continue
        details_by_slug[slug] = details
        time.sleep(0.4)

    written = 0
    for topic, frontend_id, slug, problem_dir in missing:
        details = details_by_slug.get(slug)
        if not details:
            continue
        path = write_readme(
            problem_dir,
            details["frontend_id"] or frontend_id,
            details["title"] or slug,
            slug,
            details["difficulty"],
            details["content_html"],
            details["example_testcases"],
            details["tags"],
        )
        if path:
            print(f"  -> {path}")
            written += 1

    print(f"\nDone. Wrote {written} README.md file(s).")
    print("Remember to 'git add . && git commit && git push' to save these.")


if __name__ == "__main__":
    main()
