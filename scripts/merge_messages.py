"""CLI: merge a draft payload into a next-intl messages file, surgically.

The merge itself lives in src/messages_merge.py (single source of truth, also
used by src/publish_drafts.py); this file is only the command-line front end.

`nl.json` / `fr.json` / `ru.json` are hand-formatted with 4-space indentation
and are large; re-serialising the whole file produces a giant meaningless diff.
Only the top-level namespace block(s) that actually change are replaced, every
other byte is left untouched, the file is backed up first, and anything that
does not parse as JSON is refused. Dotted keys ("EmployersPage.title") are
expanded to nested objects.

Merge semantics (safe by default): existing non-empty values WIN. New keys are
added, missing sub-keys are filled in. Use --force to let a draft overwrite.

Usage:
  python scripts/merge_messages.py \
      --file "/path/to/site/src/messages/ru.json" \
      --payload drafts/TradeNation_gevel__ru.json \
      [--force] [--dry-run]

(The publisher additionally wraps payloads that do not name their namespace,
based on the draft file name -- see messages_merge.prepare_payload. Here the
payload's top-level keys are taken as-is.)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))

from messages_merge import merge_file  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="target messages json")
    ap.add_argument("--payload", required=True,
                    help="draft json (top-level key -> subtree)")
    ap.add_argument("--force", action="store_true",
                    help="let the draft overwrite existing values")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.payload, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not payload:
        sys.exit("payload must be a non-empty object of top-level keys")

    merge_file(args.file, payload, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
