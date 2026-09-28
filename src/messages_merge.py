"""Surgical, formatting-preserving merge for next-intl messages files.

`nl.json` / `fr.json` / `ru.json` are hand-formatted with 4-space indentation
and are large (50-70 KB); re-serialising the whole file produces a giant
meaningless diff. This module therefore replaces only the top-level namespace
block(s) that actually change, keeps every other byte untouched, backs the file
up first, and refuses to write anything that does not parse as JSON.

Merge semantics (safe by default): existing non-empty values WIN. New keys are
added, missing sub-keys are filled in. Pass force=True to let a draft overwrite
existing values.

Dotted keys ("EmployersPage.title") are expanded to nested objects BEFORE the
merge, so a literal "EmployersPage.title" key can never reach a messages file.

Used by scripts/merge_messages.py (CLI) and src/publish_drafts.py (publisher).
"""
import datetime as dt
import json
import os
import re
import shutil

# Locales the site ships. The first one is the fallback for a draft whose file
# name carries no `__<lang>` suffix.
LOCALES = ("nl", "fr", "ru")

# Namespaces a draft may target. A payload whose top level already names one of
# these is considered wrapped; anything else is wrapped from the file name (see
# prepare_payload). Taken from the site's src/messages/*.json.
KNOWN_NAMESPACES = (
    "HomePage", "Navigation", "Hero", "ValueProps", "HowItWorks",
    "FeaturedJobs", "Testimonials", "TrustSignals", "CandidatesPage",
    "EmployersPage", "Services", "VacanciesPage", "AboutPage", "NewsPage",
    "ContactPage", "PrivacyPage", "Team", "Values", "Footer",
    "CandidateForm", "CookieBanner", "Metadata", "CitySEO_var1",
    "CitySEO_var2", "CitySEO_var3", "CitiesSeo", "CitySeoUi", "CityRegio",
    "EmployersUsp", "Trades", "WorkerFaq", "TradeNation",
    "SubcontractorForm", "TradeCity", "SousTraitance",
)


# ------------------------------------------------------------------ layout
def top_level_span(raw, key):
    """Byte span of the top-level `"key": {...}` block, or None if absent.

    A top-level key always starts with exactly four spaces; anything nested is
    indented deeper, so `^ {4}"` can only match a top-level key.
    """
    m = re.search(r'^ {4}"%s": ' % re.escape(key), raw, re.M)
    if not m:
        return None
    nxt = re.search(r'^ {4}"|^\}', raw[m.end():], re.M)
    if not nxt:
        raise RuntimeError(f"cannot find end of block for {key!r}")
    return m.start(), m.end() + nxt.start()


def block_text(key, obj):
    """Serialise one top-level key at the file's 4-space top-level indent."""
    dumped = json.dumps({key: obj}, indent=4, ensure_ascii=False)
    return dumped[1:-1].strip("\n")


# ------------------------------------------------------------------- merge
def merge(existing, incoming, path, force, log):
    """Recursive merge; returns (merged, [added, filled, kept, overwritten])."""
    if isinstance(existing, dict) and isinstance(incoming, dict):
        out = dict(existing)
        stats = [0, 0, 0, 0]
        for k, v in incoming.items():
            here = f"{path}.{k}" if path else k
            if k in out:
                out[k], sub = merge(out[k], v, here, force, log)
                stats = [a + b for a, b in zip(stats, sub)]
            else:
                out[k] = v
                log.append(f"  + added {here}")
                stats[0] += 1 if not isinstance(v, dict) else 0
                if isinstance(v, dict):
                    stats[0] += sum(1 for _ in _leaves(v))
        return out, stats

    # leaf vs leaf / list vs list
    empty = existing in (None, "", [], {})
    if empty or force:
        log.append(f"  ~ {'overwrote' if not empty else 'filled'} {path}")
        return incoming, [0, 1, 0, 1] if not empty else [0, 1, 0, 0]
    if existing == incoming:
        return existing, [0, 0, 0, 0]
    log.append(f"  = kept existing {path}")
    return existing, [0, 0, 1, 0]


def _leaves(d, path=""):
    for k, v in d.items():
        here = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            yield from _leaves(v, here)
        else:
            yield here


def deep_union(a, b):
    """Recursive merge of two payload subtrees; values from `b` win."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = deep_union(out[k], v) if k in out else v
        return out
    return b


def expand_dotted_keys(payload, sep="."):
    """Expand dotted keys into nested objects, at every depth.

    {"EmployersPage.title": "x"} -> {"EmployersPage": {"title": "x"}}

    Two spellings of the same leaf (a dotted key next to its nested form) are
    merged; a genuine clash (a string where an object is needed) raises, so the
    caller can refuse the draft instead of writing garbage.
    """
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            value = expand_dotted_keys(value, sep)
            parts = str(key).split(sep)
            node = out
            for part in parts[:-1]:
                nxt = node.get(part)
                if nxt is None:
                    nxt = {}
                    node[part] = nxt
                if not isinstance(nxt, dict):
                    raise ValueError(
                        f"dotted key {key!r} conflicts with a value at {part!r}")
                node = nxt
            leaf = parts[-1]
            if leaf in node and isinstance(node[leaf], dict) \
                    and isinstance(value, dict):
                node[leaf] = deep_union(node[leaf], value)
            elif leaf in node:
                raise ValueError(f"duplicate key {key!r} after expansion")
            else:
                node[leaf] = value
        return out
    if isinstance(payload, list):
        return [expand_dotted_keys(v, sep) for v in payload]
    return payload


def merge_payload(raw, payload, force=False):
    """Merge `payload` into messages JSON text `raw`, surgically.

    Returns (new_raw, stats, log). `stats` has added/filled/kept/overwritten
    and `changed` (= added + filled + overwritten). Pure: no file is touched,
    and the returned text is guaranteed to parse as JSON.
    """
    data = json.loads(raw)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload must be a non-empty object of top-level keys")
    payload = expand_dotted_keys(payload)

    log = []
    added = filled = kept = overwritten = 0
    new_raw = raw
    for key, subtree in payload.items():
        existing = data.get(key)
        if existing is None:
            merged = subtree
            log.append(f"  + added top-level {key}")
            added += max(1, sum(1 for _ in _leaves(subtree)))
        else:
            merged, stats = merge(existing, subtree, key, force, log)
            added += stats[0]
            filled += stats[1]
            kept += stats[2]
            overwritten += stats[3]

        span = top_level_span(new_raw, key)
        if span:
            # keep whatever followed the block: its separator comma (if it is
            # not the last namespace) plus the whitespace after it. Stripping
            # trailing whitespace alone would eat that comma and break the file
            # for every namespace except the last one.
            seg = new_raw[span[0]:span[1]]
            suffix = re.search(r"[,\s]*$", seg).group(0)
            new_raw = (new_raw[:span[0]] + block_text(key, merged)
                       + suffix + new_raw[span[1]:])
        else:  # brand-new top-level key -> append before the final brace
            head = new_raw.rstrip()
            if not head.endswith("}"):
                raise ValueError("unexpected file ending (no closing brace)")
            head = head[:-1].rstrip()
            new_raw = head + ",\n" + block_text(key, merged) + "\n}\n"

    json.loads(new_raw)  # never return text that does not parse
    result = {"added": added, "filled": filled, "kept": kept,
              "overwritten": overwritten}
    result["changed"] = added + filled + overwritten
    return new_raw, result, log


def merge_file(path, payload, force=False, dry_run=False):
    """Merge a payload dict (or payload JSON path) into `path` on disk.

    Backs the file up, refuses no-op and unparseable writes. Returns the stats
    from merge_payload plus 'wrote' / 'backup'.
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if isinstance(payload, str):
        with open(payload, encoding="utf-8") as f:
            payload = json.load(f)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload must be a non-empty object of top-level keys")

    new_raw, stats, log = merge_payload(raw, payload, force=force)
    print("\n".join(log))
    print(f"\n{path}: added {stats['added']}, filled {stats['filled']}, "
          f"kept {stats['kept']}, overwritten {stats['overwritten']}")

    stats["wrote"] = False
    stats["backup"] = None
    if dry_run:
        print("dry run: nothing written")
        return stats
    if stats["changed"] == 0:
        print("nothing to change: file left untouched")
        return stats

    backup = f"{path}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_raw)
    stats["wrote"] = True
    stats["backup"] = backup
    print(f"wrote {path} (backup: {backup})")
    return stats


# ------------------------------------------------------------- draft names
def locale_from_filename(name, locales=LOCALES):
    """(locale, stem) for a draft file name.

    `werkgevers__fr.json` -> ("fr", "werkgevers")
    `TradeNation.ru.json` -> ("ru", "TradeNation")   (legacy dot form)
    `city__gent.json`     -> ("nl", "city__gent")    (no suffix -> default)
    """
    stem = os.path.basename(name)
    if stem.endswith(".json"):
        stem = stem[:-5]
    pattern = "|".join(re.escape(l) for l in locales)
    m = re.search(r"__(%s)$" % pattern, stem)
    if m:
        return m.group(1), stem[:m.start()]
    m = re.search(r"\.(%s)$" % pattern, stem)
    if m:
        return m.group(1), stem[:m.start()]
    return locales[0], stem


def namespace_from_name(stem):
    """Namespace implied by a draft file name, or None.

    `TradeCity_gevel` -> "TradeCity", `TradeNation.ru` -> "TradeNation".
    """
    for ns in sorted(KNOWN_NAMESPACES, key=len, reverse=True):
        if stem == ns or stem.startswith(ns + "_") or stem.startswith(ns + "."):
            return ns
    return None


def prepare_payload(payload, name, target_keys=()):
    """Normalise one draft: expand dotted keys, wrap namespace-less payloads.

    Returns (payload, wrapper): `wrapper` is the namespace the payload was
    wrapped under, or None when the payload already named its namespaces.

    The site's own draft generators wrote two conventions: wrapped drafts
    ({"TradeCity": {"gevel": {...}}}) and bare payloads ({"gevel": {...}} /
    {"EmployersPage.title": ...}). Both are handled here so nothing can land as
    a bogus top-level key such as "gevel".
    """
    lang, stem = locale_from_filename(name)
    expanded = expand_dotted_keys(payload)
    known = set(KNOWN_NAMESPACES) | set(target_keys)
    ns = namespace_from_name(stem)
    wrapped = any(k in known for k in expanded)
    if ns and ns not in expanded and not wrapped:
        return {ns: expanded}, ns
    return expanded, None


def unknown_namespaces(payload, target_keys=()):
    """Top-level keys of a prepared payload that are not known namespaces."""
    known = set(KNOWN_NAMESPACES) | set(target_keys)
    return [k for k in payload if k not in known]
