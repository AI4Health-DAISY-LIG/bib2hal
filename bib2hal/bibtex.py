"""Minimal, dependency-free BibTeX reader.

Good enough for exports from Google Scholar, Zotero, ORCID and publishers.
Not a full BibTeX implementation (no @string macro expansion, no crossref).
"""
from __future__ import annotations

import re
import unicodedata

_ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
    "c": "\u0327", "k": "\u0328",
}
_SYMBOLS = {
    r"\&": "&", r"\%": "%", r"\$": "$", r"\#": "#", r"\_": "_",
    r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\ae": "æ", r"\AE": "Æ",
    r"\oe": "œ", r"\OE": "Œ", r"\aa": "å", r"\AA": "Å", r"\l": "ł", r"\L": "Ł",
    r"\i": "ı", "--": "–", "~": " ",
}


def latex_to_unicode(s: str) -> str:
    """Convert the common LaTeX accent/symbol macros to Unicode and drop braces."""
    if not s:
        return ""
    # \'{e}  \'e  {\'e}  \c{c}  \c c
    def repl(m: re.Match) -> str:
        mark, letter = m.group(1), m.group(2) or m.group(3)
        if letter == "\\i":
            letter = "i"
        return unicodedata.normalize("NFC", letter + _ACCENTS[mark])

    s = re.sub(r"\\([" + re.escape("'`^\"~=.") + r"]|[uvHck](?=[\s{]))\s*(?:\{(\\i|[A-Za-z])\}|(\\i|[A-Za-z]))",
               repl, s)
    for k in sorted(_SYMBOLS, key=len, reverse=True):
        if k.startswith("\\") and k[-1].isalpha():   # \ss, \o ... a following space is a terminator
            s = re.sub(re.escape(k) + r"(?![A-Za-z])\s?", lambda _m, v=_SYMBOLS[k]: v, s)
        elif k.startswith("\\"):
            s = s.replace(k, _SYMBOLS[k])
        else:
            s = s.replace(k, _SYMBOLS[k])
    s = re.sub(r"\\(?:textit|textbf|emph|textrm|mathrm|text)\s*", "", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def _read_value(text: str, i: int) -> tuple[str, int]:
    """Read one field value starting at text[i]; returns (raw value, new index)."""
    parts = []
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        c = text[i]
        if c == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            parts.append(text[i + 1:j - 1])
            i = j
        elif c == '"':
            depth, j = 0, i + 1
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                elif text[j] == '"' and depth == 0:
                    break
                j += 1
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[^,#}\s]+", text[i:])
            parts.append(m.group(0) if m else "")
            i += len(m.group(0)) if m else 1
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse_bibtex(text: str) -> list[dict]:
    """Parse BibTeX text into a list of dicts with keys ENTRYTYPE, ID and lower-cased fields."""
    entries = []
    for m in re.finditer(r"@\s*([A-Za-z]+)\s*([{(])", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        i = m.end()
        km = re.match(r"\s*([^,\s]*)\s*,", text[i:])
        if not km:
            continue
        entry = {"ENTRYTYPE": etype, "ID": km.group(1)}
        i += km.end()
        n = len(text)
        while i < n:
            fm = re.match(r"\s*([A-Za-z][\w\-:.]*)\s*=\s*", text[i:])
            if not fm:
                break
            name = fm.group(1).lower()
            i += fm.end()
            raw, i = _read_value(text, i)
            entry[name] = raw.strip()
            sm = re.match(r"\s*,?", text[i:])
            i += sm.end()
            if i < n and text[i] in "})":
                break
        entries.append(entry)
    return entries


def split_authors(field: str) -> list[dict]:
    """'Muller, Sandrine and J. Doe' -> [{'family': 'Muller', 'given': 'Sandrine'}, ...]"""
    if not field:
        return []
    out = []
    for name in re.split(r"\s+and\s+", field.strip(), flags=re.I):
        name = latex_to_unicode(name).strip()
        if not name or name.lower() == "others":
            continue
        if "," in name:
            fam, giv = name.split(",", 1)
        else:
            bits = name.split()
            fam, giv = (bits[-1], " ".join(bits[:-1])) if len(bits) > 1 else (name, "")
        out.append({"family": fam.strip(), "given": giv.strip()})
    return out


_BIB_TYPES = {"ART": "article", "COMM": "inproceedings", "POSTER": "inproceedings", "COUV": "incollection",
              "OUV": "book", "DOUV": "book", "THESE": "phdthesis", "HDR": "phdthesis", "MEM": "mastersthesis",
              "REPORT": "techreport", "UNDEFINED": "misc", "OTHER": "misc"}


def _bib_escape(v: str) -> str:
    v = (v or "").replace("{", "").replace("}", "")      # values are already plain Unicode text
    return re.sub(r"(?<!\\)([&%#])", r"\\\1", v)


def write_bibtex(rows: list[dict]) -> str:
    """Reviewed rows -> BibTeX text (corrected metadata, DOIs found, your decisions kept as a note)."""
    out = []
    for r in rows:
        t = r.get("hal_type") or ""
        etype = _BIB_TYPES.get(t, r.get("bib_type") or "misc")
        authors = " and ".join(a.strip() for a in (r.get("authors") or "").split(";") if a.strip())
        venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle",
                       "misc": "howpublished"}.get(etype, "")
        fields = [("title", r.get("title")), ("author", authors), ("year", r.get("year")),
                  (venue_field, r.get("venue") if venue_field else ""), ("volume", r.get("volume")),
                  ("number", r.get("issue")), ("pages", r.get("pages")), ("publisher", r.get("publisher")),
                  ("school" if etype in ("phdthesis", "mastersthesis") else "institution",
                   r.get("institution") if etype in ("phdthesis", "mastersthesis", "techreport") else ""),
                  ("address", ", ".join(x for x in (r.get("conf_city"), r.get("conf_country")) if x)),
                  ("doi", r.get("doi")), ("issn", r.get("issn")), ("isbn", r.get("isbn")),
                  ("abstract", r.get("abstract")),
                  ("note", f"bib2hal: {r.get('action', '')}" + (f", HAL {r['hal_id']}" if r.get("hal_id") else ""))]
        body = ",\n".join(f"  {k} = {{{_bib_escape(str(v))}}}" for k, v in fields if k and v)
        out.append(f"@{etype}{{{r.get('key') or 'ref'},\n{body}\n}}")
    return "% Exported by bib2hal (reviewed and completed references)\n\n" + "\n\n".join(out) + "\n"
