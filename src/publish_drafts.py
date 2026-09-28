"""Publisher: commit approved drafts to a branch and open a GitHub PR.

This is the *only* write action in the system. It runs only if the user
explicitly calls it -- publish is a deliberate gesture, never automatic.

Each draft is merged into the messages file of ITS OWN locale: a `__fr` /
`__ru` suffix in the draft file name picks src/messages/fr.json / ru.json,
anything else (or `__nl`) goes to nl.json. Dotted keys are expanded to nested
objects and the merge is recursive, so drafting one trade never wipes its
siblings. The merge is surgical (see src/messages_merge.py): only the top-level
namespace blocks that actually change are rewritten, the rest of the file stays
byte-for-byte identical, and only a file that parses as JSON is ever committed.

Drafts that fail Agent 3's language guard are never published: they are dropped
from the run (like generate_content.py EXCLUDES them) and the command exits
non-zero, so a mixed-language draft can never reach a messages file.

Usage:
    python src/publish_drafts.py --config config.yaml --repo /path/to/constructief
    python src/publish_drafts.py --config config.yaml --repo /path/to/constructief --dry-run
    python src/publish_drafts.py --config config.yaml --repo /path/to/constructief --force
    python src/publish_drafts.py --repo /path/to/constructief \
        --drafts drafts --site /path/to/site-copy [--dry-run]

`--site` merges into a LOCAL checkout instead of GitHub: no branch, no PR and no
network -- use it to verify a merge before pushing.

Config additions (config.yaml):
  github:
    token: "github_pat_..."        # fine-grained PAT, Contents+PR on this repo only
    repo: "aurora10/constructief"
    base_branch: "google-sheets"   # what Vercel deploys
  notify: (same as in generate_content)
"""
import argparse
import base64
import datetime as dt
import json
import os
import shutil
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from messages_merge import (deep_union, locale_from_filename,  # noqa: E402
                            merge_payload, prepare_payload,
                            unknown_namespaces)


G = "https://api.github.com"
MESSAGES_DIR = "src/messages"


# --------------------------------------------------------------- language guard
_LANGUAGE_MODULE = None


def language_module():
    """Agent 3's module, or None when it cannot be imported (guard disabled)."""
    global _LANGUAGE_MODULE
    if _LANGUAGE_MODULE is None:
        try:
            import generate_content
            _LANGUAGE_MODULE = generate_content
        except Exception as e:  # openai missing, broken checkout, ...
            print(f"  warn: language guard unavailable ({e}); drafts not "
                  f"language-checked")
            _LANGUAGE_MODULE = False
    return _LANGUAGE_MODULE or None


def draft_language_violations(frag, lang):
    """Mixed-language problems in a draft; [] when clean or guard unavailable."""
    module = language_module()
    if module is None or not hasattr(module, "language_violations"):
        return []
    return module.language_violations(frag, lang)


# ------------------------------------------------------------------- github
def gh_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def get_file(token, repo, path, ref):
    """(text, sha) of a repo file at `ref`."""
    r = requests.get(f"{G}/repos/{repo}/contents/{path}?ref={ref}",
                     headers=gh_headers(token))
    r.raise_for_status()
    body = r.json()
    return base64.b64decode(body["content"]).decode("utf-8"), body["sha"]


def put_file(token, repo, path, branch, raw, sha, message):
    """Commit exactly `raw` (the surgically edited text) to `path`."""
    payload = {
        "message": message,
        "content": base64.b64encode(raw.encode("utf-8")).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{G}/repos/{repo}/contents/{path}",
                     headers=gh_headers(token), json=payload)
    r.raise_for_status()


def merge_into_locale(raw, payload, force=False):
    """Surgically merge draft fragments into one messages file's text.

    Returns (new_text, stats, log); the text is checked to parse as JSON here,
    so a corrupt merge can never reach a commit.
    """
    new_raw, stats, log = merge_payload(raw, payload, force=force)
    json.loads(new_raw)
    return new_raw, stats, log


# --------------------------------------------------------------------- drafts
def drafts_dir(repo, override=None):
    """Where drafts live: --drafts, else <repo>/../seo-agent/drafts, else ./drafts."""
    if override:
        return override
    guess = os.path.join(repo, os.pardir, "seo-agent", "drafts")
    if os.path.isdir(guess):
        return guess
    return "drafts"


def load_drafts(path):
    """{file name: (locale, expanded payload)} for every draft in `path`."""
    if not os.path.isdir(path):
        return {}
    drafts = {}
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".json") or fn.startswith("report"):
            continue
        full = os.path.join(path, fn)
        try:
            with open(full, encoding="utf-8") as f:
                frag = json.load(f)
        except ValueError as e:
            sys.exit(f"draft {fn} is not valid JSON, nothing published: {e}")
        if not isinstance(frag, dict) or not frag:
            sys.exit(f"draft {fn} is not a non-empty object, nothing published")
        lang, _stem = locale_from_filename(fn)
        payload, wrapper = prepare_payload(frag, fn)
        drafts[fn] = (lang, payload, wrapper, frag)
    return drafts


def combine_payloads(payloads):
    """Deep-union the payloads of all drafts that target one locale."""
    out = {}
    for payload in payloads:
        out = deep_union(out, payload)
    return out


def group_by_locale(drafts):
    """{locale: [(file name, payload)]} in draft file name order."""
    grouped = {}
    for fn, (lang, payload, _wrapper, _frag) in drafts.items():
        grouped.setdefault(lang, []).append((fn, payload))
    return grouped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", required=True, help="local clone (used for drafts/)")
    ap.add_argument("--drafts", help="drafts directory (default: <repo>/../seo-agent/drafts)")
    ap.add_argument("--site", help="local site checkout: merge src/messages/*.json "
                                   "there instead of GitHub (no branch, no PR)")
    ap.add_argument("--force", action="store_true",
                    help="let drafts overwrite existing non-empty values "
                         "(default: existing values win)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate draft JSON, show what WOULD be pushed")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    gh = cfg.get("github") or {}
    token, repo, base = gh.get("token"), gh.get("repo"), gh.get("base_branch")

    drafts = load_drafts(drafts_dir(args.repo, args.drafts))
    if not drafts:
        print("No drafts to publish. Run generate_content.py first.")
        return

    print(f"Drafts to publish: {list(drafts.keys())}")
    print(f"Target: {repo or args.site or args.repo} branch={base or '-'}")

    # language guard: a mixed-language draft can never be published. Blocked
    # drafts are dropped from this run (like generate_content.py EXCLUDES them)
    # and the process still exits non-zero, so the run is never silently partial.
    blocked = []
    for fn, (lang, _payload, _wrapper, frag) in drafts.items():
        problems = draft_language_violations(frag, lang)
        if problems:
            blocked.append((fn, lang, problems))
    if blocked:
        print(f"\n!! {len(blocked)} draft(s) EXCLUDED by the language guard "
              f"(not published):")
        for fn, lang, problems in blocked:
            print(f"  - {fn} ({lang}): {problems[0]}"
                  + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""))
        for fn, _lang, _problems in blocked:
            drafts.pop(fn)
    rc = 1 if blocked else 0
    if not drafts:
        print("\nnothing left to publish.")
        sys.exit(rc)

    grouped = group_by_locale(drafts)
    dated = dt.date.today().isoformat()
    branch = f"seo/{dated}"

    if not args.site:
        print("\nDraft -> locale file:")
        for fn, (lang, payload, wrapper, _frag) in drafts.items():
            note = f" (wrapped under {wrapper})" if wrapper else ""
            print(f"  {fn}: {MESSAGES_DIR}/{lang}.json keys "
                  f"{list(payload.keys())}{note}")

    if args.dry_run and not args.site:
        print(f"\nDRY RUN — nothing pushed. New branch would be: {branch}")
        print("PR title: SEO: content drafts (Agent 3)")
        print("\n(run without --dry-run to actually create branch + open PR)")
        sys.exit(rc)

    local = args.site is not None
    if not local and not (token and repo and base):
        sys.exit("config.yaml github.token/repo/base_branch are required to push")

    # ---- merge into each locale's messages file (in memory first)
    results = []   # (lang, path, new_raw, stats, sha)
    for lang in sorted(grouped):
        items = grouped[lang]
        path = f"{MESSAGES_DIR}/{lang}.json"
        if local:
            full = os.path.join(args.site, path)
            if not os.path.isfile(full):
                sys.exit(f"no messages file at {full}")
            with open(full, encoding="utf-8") as f:
                raw = f.read()
            sha = None
        else:
            raw, sha = get_file(token, repo, path, base)

        payload = combine_payloads([p for _fn, p in items])
        bad = unknown_namespaces(payload, json.loads(raw).keys())
        if bad:
            sys.exit(f"{lang}: draft(s) {[fn for fn, _p in items]} would add "
                     f"unknown top-level key(s) {bad} to {path} — refusing")
        new_raw, stats, log = merge_into_locale(raw, payload, force=args.force)
        print(f"\n{path} from {[fn for fn, _p in items]}:")
        print("\n".join(log))
        print(f"  {path}: added {stats['added']}, filled {stats['filled']}, "
              f"kept {stats['kept']}, overwritten {stats['overwritten']}")
        if stats["changed"] == 0:
            print(f"  {path}: no change, left untouched")
            continue

        if local:
            if args.dry_run:
                print(f"  dry run: {full} left untouched")
            else:
                backup = f"{full}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}"
                shutil.copy2(full, backup)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(new_raw)
                print(f"  wrote {full} (backup: {backup})")
        results.append((lang, path, new_raw, stats, sha))

    if not results:
        print("\nnothing to merge: no branch created, no PR opened.")
        sys.exit(rc)

    if local:
        print("\n--site: local merge only, no branch, no PR, nothing pushed.")
        sys.exit(rc)

    # 1. new branch from base
    r = requests.get(f"{G}/repos/{repo}/git/refs/heads/{base}",
                     headers=gh_headers(token))
    r.raise_for_status()
    base_sha = r.json()["object"]["sha"]
    requests.post(f"{G}/repos/{repo}/git/refs",
                  headers=gh_headers(token),
                  json={"ref": f"refs/heads/{branch}",
                        "sha": base_sha}).raise_for_status()

    # 2. commit the surgically edited locale file(s) on the new branch
    body_lines = ["Drafts generated by seo-agent Agent 3. Review the "
                  "`src/messages/*.json` diffs, merge to deploy.", ""]
    for fn, (lang, payload, wrapper, _frag) in drafts.items():
        body_lines.append(f"- `{fn}` -> `{MESSAGES_DIR}/{lang}.json` "
                          f"(namespaces: {', '.join(sorted(payload))})")
    body_lines.append("")
    for lang, path, new_raw, stats, sha in results:
        put_file(token, repo, path, branch, new_raw, sha,
                 f"SEO: content drafts for {lang} ({len(drafts)})")
        body_lines.append(f"- `{path}`: added {stats['added']}, "
                          f"filled {stats['filled']}, kept {stats['kept']}, "
                          f"overwritten {stats['overwritten']}")

    # 3. open PR
    pr = requests.post(f"{G}/repos/{repo}/pulls",
                       headers=gh_headers(token),
                       json={"title": f"SEO: content drafts {dated}",
                             "head": branch, "base": base,
                             "body": "\n".join(body_lines)}).json()
    print(f"\nPR opened: {pr['html_url']}")
    print("Merge it on GitHub to deploy.")
    if blocked:
        print(f"note: {len(blocked)} draft(s) were excluded by the language "
              f"guard and are NOT in this PR.")
    sys.exit(rc)


if __name__ == "__main__":
    main()
