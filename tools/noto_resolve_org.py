"""Resolve still-missing Noto faces against the notofonts org repositories.

Reads .noto-cache/repos*.json (fetched by the shell) and rewrites .noto-cache/urls.txt with URLs
for the faces that are not cached yet, using the org's per-script repo layout:

    https://raw.githubusercontent.com/notofonts/<repo>/main/fonts/<Family>/<hinted|unhinted>/ttf/<file>
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CACHE = os.path.join(ROOT, ".noto-cache")
RAW = "https://raw.githubusercontent.com/notofonts/"


def main() -> int:
    repos: dict[str, str] = {}
    for path in glob.glob(os.path.join(CACHE, "repos*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                for r in json.load(fh):
                    repos[r["name"].lower().replace("-", "").replace("_", "")] = r["name"]
        except Exception:
            continue
    print(f"notofonts repos: {len(repos)}")

    with open(os.path.join(CACHE, "urls.txt"), encoding="utf-8") as fh:
        members = [line.split("\t")[0] for line in fh]

    out = []
    unresolved = []
    for member in members:
        if os.path.exists(os.path.join(CACHE, member)):
            continue
        family = member.split("-")[0]
        key = family.lower().replace("noto", "", 1).replace("sans", "", 1).replace("serif", "", 1)
        repo = repos.get(key) or repos.get(key + "s")
        if not repo:
            unresolved.append(member)
            continue
        for kind in ("hinted", "unhinted"):
            out.append((member, f"{RAW}{repo}/main/fonts/{family}/{kind}/ttf/{member}"))
    with open(os.path.join(CACHE, "urls.txt"), "w", encoding="utf-8") as fh:
        for member, url in out:
            fh.write(f"{member}\t{url}\n")
    print(f"resolved {len({m for m, _ in out})} faces over {len(out)} urls; "
          f"{len(unresolved)} without a repo guess")
    print("no repo guess for:", ", ".join(unresolved[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
