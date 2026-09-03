"""Browse a folder of reference fonts and render individual glyphs as previews.

This is a helper for the editor: it lets the author point the GUI at a folder of installed
or downloaded fonts, then see how a specific codepoint looks in whichever reference font
actually has it, and use that as a drawing guide for the stroke glyph.

Rendering and character-membership use **FreeType (via Pillow)** and **HarfBuzz (via
uharfbuzz)** — never ``fontTools``' TTF/OTF code paths.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PIL import Image, ImageDraw, ImageFont


def _os2_cap_height(path: str) -> Optional[int]:
    """Read the OS/2 ``sCapHeight`` (in font units) from a single-font sfnt without fontTools.

    Falls back to ``None`` for collections (``.ttc``) or fonts without OS/2, so callers can
    fall back to measuring a glyph.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) < 12 or data[0:4] == b"ttcf":
            return None
        num_tables = struct.unpack(">H", data[4:6])[0]
        for i in range(num_tables):
            rec = data[12 + 16 * i: 12 + 16 * i + 16]
            if rec[0:4] == b"OS/2":
                offset = struct.unpack(">I", rec[8:12])[0]
                if offset + 90 <= len(data):
                    version = struct.unpack(">H", data[offset:offset + 2])[0]
                    cap = struct.unpack(">h", data[offset + 88:offset + 90])[0]
                    if version >= 2 and cap > 0:
                        return cap
                return None
        return None
    except Exception:
        return None

try:  # uharfbuzz is optional but strongly recommended
    import uharfbuzz as _hb
except Exception:  # pragma: no cover
    _hb = None

SUPPORTED_EXTS = {".ttf", ".otf", ".ttc", ".woff", ".woff2", ".otc"}


class ReferenceFont:
    """A single reference font file, lazily opened for rendering + membership checking."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.ext = self.path.suffix.lower()
        self._pil: Optional[ImageFont.ImageFont] = None
        self._hb_face = None
        self._hb_font = None
        self._upem_value: Optional[int] = None
        self._family = None
        self._covered: Set[int] = set()
        self._absent: Set[int] = set()

    @property
    def family(self) -> str:
        if self._family is None:
            try:
                f = self._pil_font()
                self._family = f.getname()[0]
            except Exception:
                self._family = self.path.stem
        return self._family

    def _pil_font(self, size: int = 128) -> ImageFont.ImageFont:
        if self._pil is None:
            self._pil = ImageFont.truetype(str(self.path), size)
        return self._pil

    def _hb_font_instance(self):
        if _hb is None:
            return None
        if self._hb_font is None:
            data = self.path.read_bytes()
            face = _hb.Face(data)
            font = _hb.Font(face)
            upem = getattr(face, "upem", None) or 1000
            self._upem_value = upem
            try:
                font.scale = (upem, upem)
            except Exception:
                pass
            if hasattr(_hb, "ot_font_set_funcs"):
                try:
                    _hb.ot_font_set_funcs(font)
                except Exception:
                    pass
            self._hb_font = font
        return self._hb_font

    def _upem(self) -> int:
        """Units-per-em for this font (1000 fallback)."""
        self._hb_font_instance()
        return self._upem_value or 1000

    def has(self, codepoint: int) -> bool:
        """True if this font maps ``codepoint`` to a real glyph (not .notdef)."""
        cp = int(codepoint)
        if cp in self._covered:
            return True
        if cp in self._absent:
            return False
        if 0xD800 <= cp <= 0xDFFF:
            self._absent.add(cp)
            return False
        font = self._hb_font_instance()
        if font is None:
            # fall back to FreeType: attempt to render and compare to .notdef
            return self._render_present(cp)
        try:
            gid = font.get_nominal_glyph(cp)
        except Exception:
            gid = 0
        if gid and gid != 0:
            self._covered.add(cp)
            return True
        self._absent.add(cp)
        return False

    def _render_present(self, cp: int) -> bool:
        """Best-effort membership using FreeType only (no HarfBuzz installed)."""
        try:
            f = self._pil_font()
            bbox = f.getbbox(chr(cp))
            return bool(bbox) and bbox[2] > bbox[0] and bbox[3] > bbox[1]
        except Exception:
            return False

    def render(self, codepoint: int, pixel_size: int = 96, pad: int = 4) -> Optional[Image.Image]:
        """Render ``codepoint`` as a transparent RGBA bitmap, cropped to the glyph.

        Returns ``None`` if the font does not contain the glyph.
        """
        if not self.has(codepoint):
            return None
        cp = int(codepoint)
        try:
            f = self._pil_font(pixel_size)
        except Exception:
            return None
        ch = chr(cp)
        try:
            bbox = f.getbbox(ch)
        except Exception:
            return None
        if not bbox:
            return None
        left, top, right, bottom = bbox
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        mask = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
        d = ImageDraw.Draw(mask)
        d.text((pad - left, pad - top), ch, font=f, fill=255)
        # black glyph where the mask is opaque, transparent elsewhere
        out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
        out.putalpha(mask)
        return out

    def render_in_box(
        self,
        codepoint: int,
        box_px: int = 64,
        pixel_size: int = 96,
        pad: int = 4,
    ) -> Optional[Image.Image]:
        """Render the glyph fitted (aspect preserved) into a ``box_px`` square."""
        glyph = self.render(codepoint, pixel_size, pad)
        if glyph is None or glyph.width == 0 or glyph.height == 0:
            return None
        # center in a box_px square
        inner = min(box_px - 8, glyph.size[0], glyph.size[1])
        scale = inner / max(glyph.size[0], glyph.size[1])
        nw, nh = max(1, int(glyph.size[0] * scale)), max(1, int(glyph.size[1] * scale))
        glyph = glyph.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGBA", (box_px, box_px), (0, 0, 0, 0))
        canvas.paste(glyph, (box_px // 2 - nw // 2, box_px // 2 - nh // 2), glyph)
        return canvas

    def glyph_bitmap(self, codepoint: int, em_px: int = 160):
        """Render a glyph while retaining its vertical metrics.

        Returns ``(image, baseline_px, capheight_px)``: ``image`` is an RGBA bitmap whose
        *baseline* is ``baseline_px`` pixels below the top, and ``capheight_px`` is this
        font's cap-height in the same pixel space (measured from a capital ``H``). This lets
        a caller place the glyph against its own baseline and scale it by its own cap-height
        (rather than fit-and-centre). Returns ``None`` if the font lacks the codepoint.
        """
        if not self.has(codepoint):
            return None
        f = self._pil_font(em_px)
        asc, desc = f.getmetrics()
        W = max(em_px * 2, em_px + 64)
        H = asc + desc
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        d.text((em_px // 2, 0), chr(codepoint), font=f, fill=255)
        ibox = mask.getbbox()
        if not ibox:
            return None
        baseline_px = asc
        # Prefer a reliable, glyph-independent cap-height (OS/2 sCapHeight). Rendering a
        # specific capital like "H" is wrong when the font doesn't contain it (e.g. an
        # SMP-only font such as unifont_upper) — it would measure the .notdef box instead.
        os2_cap = _os2_cap_height(str(self.path))
        if os2_cap:
            cap_px = os2_cap * (float(em_px) / self._upem())
        else:
            hb = f.getbbox("H")
            cap_px = (hb[3] - hb[1]) if (hb and hb[3] > hb[1]) else asc
        crop = mask.crop((ibox[0], 0, ibox[2], H))
        out = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        out.putalpha(crop)
        return out, float(baseline_px), float(cap_px)


class ReferenceLibrary:
    """A list of reference fonts indexed from one or more folders."""

    def __init__(self, folders: Optional[Sequence[str]] = None) -> None:
        self.fonts: List[ReferenceFont] = []
        if folders:
            self.add_folders(folders)

    def add_folders(self, folders: Sequence[str], recursive: bool = True) -> int:
        """Scan ``folders`` and add any supported font files. Returns count added."""
        n = 0
        for folder in folders:
            root = Path(folder)
            if not root.is_dir():
                continue
            it = root.rglob("*") if recursive else root.glob("*")
            for p in it:
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                    if not self._already(p):
                        self.fonts.append(ReferenceFont(str(p)))
                        n += 1
        return n

    def _already(self, path: Path) -> bool:
        rp = path.resolve()
        return any(f.path.resolve() == rp for f in self.fonts)

    def clear(self) -> None:
        self.fonts.clear()

    def __len__(self) -> int:
        return len(self.fonts)

    def __iter__(self):
        return iter(self.fonts)

    def has(self, codepoint: int) -> bool:
        return any(f.has(codepoint) for f in self.fonts)

    def first_with(self, codepoint: int) -> Optional[ReferenceFont]:
        for f in self.fonts:
            if f.has(codepoint):
                return f
        return None

    def render_first(
        self, codepoint: int, box_px: int = 64, pixel_size: int = 96
    ) -> Optional[Image.Image]:
        f = self.first_with(codepoint)
        if f is None:
            return None
        return f.render_in_box(codepoint, box_px, pixel_size)

    def coverage(self, codepoints: Iterable[int]) -> Set[int]:
        """Return the subset of ``codepoints`` present in any reference font."""
        cps = set(codepoints)
        out: Set[int] = set()
        for cp in cps:
            if self.has(cp):
                out.add(cp)
        return out

    def coverage_in_range(self, start: int, end: int, stride: int = 1) -> List[int]:
        cps = range(start, end + 1, stride)
        return sorted(cp for cp in cps if self.has(cp))
