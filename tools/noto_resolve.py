"""Resolve the remaining Noto faces against the google/fonts file tree.

Reads .noto-cache/tree.json (fetched by the shell) and rewrites .noto-cache/urls.txt with the real
raw paths for every face still missing from the cache.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CACHE = os.path.join(ROOT, ".noto-cache")
RAW = "https://raw.githubusercontent.com/google/fonts/main/"


def main() -> int:
    with open(os.path.join(CACHE, "tree.json"), encoding="utf-8") as fh:
        tree = json.load(fh)
    paths = [e["path"] for e in tree["tree"] if e["type"] == "blob" and e["path"].endswith(".ttf")]

    def slug_of(path: str) -> str:
        parts = path.split("/")
        return parts[1].lower() if len(parts) > 2 and parts[0] == "ofl" else ""

    by_dir: dict[str, list[str]] = {}
    for p in paths:
        by_dir.setdefault(slug_of(p), []).append(p)

    want = []
    with open(os.path.join(CACHE, "urls.txt"), encoding="utf-8") as fh:
        for line in fh:
            member = line.split("\t")[0]
            if os.path.exists(os.path.join(CACHE, member)):
                continue
            want.append(member)

    out = []
    resolved = 0
    for member in want:
        family = member.split("-")[0]
        slug = "".join(ch for ch in family.lower() if ch.isalnum())
        cands = [p for p in by_dir.get(slug, []) if "Italic" not in p]
        pick = None
        for p in cands:
            if os.path.basename(p) == member:
                pick = p
                break
        if pick is None:
            for p in cands:
                if os.path.basename(p).endswith("Regular.ttf") or "-Regular" in p:
                    pick = p
                    break
        if pick is None and cands:
            pick = sorted(cands)[0]
        if pick:
            resolved += 1
            out.append((member, RAW + pick))
            print(f"  {member:<42} -> {pick}")
        else:
            print(f"  {member:<42} -> not in google/fonts")

    with open(os.path.join(CACHE, "urls.txt"), "w", encoding="utf-8") as fh:
        for member, url in out:
            fh.write(f"{member}\t{url}\n")
    print(f"\nresolved {resolved} of {len(want)} remaining faces; urls.txt rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
