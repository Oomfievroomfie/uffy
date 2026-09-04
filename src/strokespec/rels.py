"""Discover "related glyphs" for a codepoint.

Two relations are provided:

* **Decomposition base** — for a precombined glyph+diacritic (e.g. ``á``) the related glyph is
  its base letter (the first element of the Unicode canonical/compatibility decomposition).
* **Hanzi components** — for a CJK Unified Ideograph the related glyphs are the direct
  Ideographic Description Sequence (IDS) components from the **cjkvi-ids** database
  (bundled as ``data/ids.txt``), e.g. ``明`` -> ``日``, ``月``.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Dict, List, Optional, Tuple

# IDS data is parsed once and cached in-process (88k lines -> a codepoint->components map).
_IDS_PATH = os.path.join(os.path.dirname(__file__), "data", "ids.txt")

# Ideographic Description Characters (U+2FF0..U+2FFB) — operators in an IDS string.
_IDC_START = 0x2FF0
_IDC_END = 0x2FFB


def _is_idc(ch: str) -> bool:
    return _IDC_START <= ord(ch) <= _IDC_END


def _parse_terms(s: str, i: int) -> Tuple[List[tuple], int]:
    """Parse a run of IDS terms starting at ``i``.

    A term is either a single character (``("char", codepoint)``) or an operator followed by
    its operand terms (``("compound", [terms])``). Returns ``(terms, next_index)``.
    """
    terms: List[tuple] = []
    n = len(s)
    while i < n:
        ch = s[i]
        if _is_idc(ch):
            kids, i = _parse_terms(s, i + 1)
            terms.append(("compound", kids))
        else:
            terms.append(("char", ord(ch)))
            i += 1
    return terms, i


def _ids_components(ids: str) -> List[int]:
    """The direct component codepoints of an IDS string (the top-level operands).

    The root is the leading operator; its operand terms that are single characters are the
    direct components. Nested compounds are not descended into (they are not *direct*), and a
    lone-character "IDS" has no components.
    """
    terms, _ = _parse_terms(ids, 0)
    if not terms or terms[0][0] != "compound":
        return []
    out = [t[1] for t in terms[0][1] if t[0] == "char"]
    return out


# codepoint -> list of direct component codepoints (lazy, cached)
_IDS_CACHE: Optional[Dict[int, List[int]]] = None


def _load_ids() -> Dict[int, List[int]]:
    global _IDS_CACHE
    if _IDS_CACHE is not None:
        return _IDS_CACHE
    d: Dict[int, List[int]] = {}
    try:
        fh = open(_IDS_PATH, encoding="utf-8")
    except OSError:
        _IDS_CACHE = {}
        return _IDS_CACHE
    with fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "\t" not in line:
                continue
            cp_s, _, ids = line.split("\t", 2)
            if not cp_s.startswith("U+"):
                continue
            try:
                cp = int(cp_s[2:], 16)
            except ValueError:
                continue
            comps = _ids_components(ids)
            if comps:
                d[cp] = comps
    _IDS_CACHE = d
    return d


def decomposition_base(cp: int) -> Optional[int]:
    """The base codepoint of a precombined character, else ``None``."""
    dec = unicodedata.decomposition(chr(cp))
    if not dec:
        return None
    nums = [p for p in dec.split() if not p.startswith("<")]
    if not nums:
        return None
    try:
        base = int(nums[0], 16)
    except ValueError:
        return None
    return base if base != cp else None


def ids_components(cp: int) -> List[int]:
    """Direct hanzi IDS components of ``cp``, else ``[]``."""
    return _load_ids().get(cp, [])


def related_codepoints(cp: int) -> List[int]:
    """Related codepoints for ``cp`` (base char + hanzi IDS components), de-duplicated."""
    out: List[int] = []
    base = decomposition_base(cp)
    if base is not None:
        out.append(base)
    for c in ids_components(cp):
        if c != cp:
            out.append(c)
    seen: set = set()
    res: List[int] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res
