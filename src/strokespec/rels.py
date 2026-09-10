"""Discover "related glyphs" for a codepoint.

Three relations are provided:

* **Decomposition base** — for a precombined glyph+diacritic (e.g. ``á``) the related glyph is
  its base letter (the first element of the Unicode canonical/compatibility decomposition).
* **Hanzi components** — for a CJK Unified Ideograph the related glyphs are the direct
  Ideographic Description Sequence (IDS) components from the **cjkvi-ids** database
  (bundled as ``data/ids.txt``), e.g. ``明`` -> ``日``, ``月``.
* **Hangul jamo** — for a Hangul syllable (U+AC00..U+D7A3) the related glyphs are its
  conjoining jamo (leading consonant, vowel, optional trailing consonant). This decomposition
  is *algorithmic* (Unicode Standard §3.12 "Conjoining Jamo Behavior"), so it is computed from
  constants rather than read from a table — UnicodeData.txt does not list it (the block is
  collapsed to ``<Hangul Syllable, First/Last>`` with an empty decomposition field).
"""

from __future__ import annotations

import os
import unicodedata2 as unicodedata
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
    """The component codepoints of an IDS string — every leaf character in the tree.

    Compounds are descended into, so ``⿰亻⿱ユ矢`` (侯) yields ``[亻, ユ, 矢]``, not just the
    top-level operand 亻. A lone-character "IDS" has no components.
    """
    terms, _ = _parse_terms(ids, 0)
    if not terms or terms[0][0] != "compound":
        return []
    out: List[int] = []

    def walk(node: tuple) -> None:
        if node[0] == "char":
            out.append(node[1])
        else:
            for kid in node[1]:
                walk(kid)

    walk(terms[0])
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
    comps = decomposition_components(cp)
    return comps[0] if comps else None


def decomposition_components(cp: int) -> List[int]:
    """Every codepoint in a character's Unicode decomposition (base + combining marks).

    E.g. U+0168 (ũ) decomposes to ``0075 0303`` -> ``[0x75 'u', 0x303 combining tilde]``.
    Tags such as ``<compat>`` are ignored.
    """
    dec = unicodedata.decomposition(chr(cp))
    if not dec:
        return []
    out: List[int] = []
    for p in dec.split():
        if p.startswith("<"):
            continue
        try:
            v = int(p, 16)
        except ValueError:
            continue
        if v != cp:
            out.append(v)
    return out


def ids_components(cp: int) -> List[int]:
    """Direct hanzi IDS components of ``cp``, else ``[]``."""
    return _load_ids().get(cp, [])


# --- Hangul syllables (algorithmic decomposition, Unicode Standard §3.12) ------
# Conjoining Jamo constants. TBase is chosen so that trailing-consonant index 0 means "none".
HANGUL_SBASE = 0xAC00
HANGUL_LBASE = 0x1100
HANGUL_VBASE = 0x1161
HANGUL_TBASE = 0x11A7
HANGUL_LCOUNT = 19
HANGUL_VCOUNT = 21
HANGUL_TCOUNT = 28
HANGUL_NCOUNT = HANGUL_VCOUNT * HANGUL_TCOUNT   # 588
HANGUL_SCOUNT = HANGUL_LCOUNT * HANGUL_NCOUNT  # 11172


def hangul_components(cp: int) -> List[int]:
    """The conjoining jamo of a Hangul syllable, else ``[]``.

    Computed arithmetically (no table): an LV syllable yields ``[L, V]``, an LVT syllable
    ``[L, V, T]``. E.g. U+AC01 (각) -> ``[0x1100 ᄀ, 0x1161 ᅡ, 0x11A8 ᆨ]``.
    """
    if not (HANGUL_SBASE <= cp < HANGUL_SBASE + HANGUL_SCOUNT):
        return []
    i = cp - HANGUL_SBASE
    lead = HANGUL_LBASE + i // HANGUL_NCOUNT
    vowel = HANGUL_VBASE + (i % HANGUL_NCOUNT) // HANGUL_TCOUNT
    trail = i % HANGUL_TCOUNT
    comps = [lead, vowel]
    if trail:
        comps.append(HANGUL_TBASE + trail)
    return comps


def related_codepoints(cp: int) -> List[int]:
    """Related codepoints: the base + combining marks of a precombined character, the direct
    hanzi IDS components of a Han ideograph, and the conjoining jamo of a Hangul syllable.
    De-duplicated and never the char itself."""
    out: List[int] = []
    out.extend(decomposition_components(cp))
    out.extend(hangul_components(cp))
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
