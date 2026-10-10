"""Build HAL TEI import files (AOfr packaging) from reviewed rows."""
from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

from .review import authors_from_str

TEI_NS = "http://www.tei-c.org/ns/1.0"
HAL_NS = "http://hal.archives-ouvertes.fr/"
ET.register_namespace("", TEI_NS)
ET.register_namespace("hal", HAL_NS)

AUDIENCE = {"unspecified": "1", "international": "2", "national": "3"}
LICENCES = {
    "cc by": "http://creativecommons.org/licenses/by/",
    "cc by 4.0": "http://creativecommons.org/licenses/by/",
    "cc by-nc": "http://creativecommons.org/licenses/by-nc/",
    "cc by-nc-nd": "http://creativecommons.org/licenses/by-nc-nd/",
    "cc by-sa": "http://creativecommons.org/licenses/by-sa/",
    "cc0": "http://creativecommons.org/publicdomain/zero/1.0/",
    "copyright": "http://hal.archives-ouvertes.fr/licences/copyright/",
}


def _t(tag: str) -> str:
    return f"{{{TEI_NS}}}{tag}"


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, _t(tag), {k: v for k, v in attrs.items() if v is not None})
    if text:
        el.text = text
    return el


def _xml_lang(el, lang):
    el.set("{http://www.w3.org/XML/1998/namespace}lang", lang or "en")


def _yes(v: str) -> str:
    return "1" if (v or "").strip().lower() in ("yes", "oui", "1", "true", "y") else "0"


def _author(parent, a: dict, structs: list[str], idhal: str, orcid: str):
    au = _sub(parent, "author", role="aut")
    pn = _sub(au, "persName")
    if a.get("given"):
        _sub(pn, "forename", a["given"], type="first")
    _sub(pn, "surname", a["family"])
    if idhal:
        _sub(au, "idno", idhal, type="idhal", notation="string")
    if orcid:
        _sub(au, "idno", f"https://orcid.org/{orcid}", type="ORCID")
    for s in structs:
        _sub(au, "affiliation", ref=f"#struct-{s}")
    return au


def build_tei(row: dict, idhal: str = "", pdf_name: str = "") -> bytes:
    """Return TEI XML bytes for one reviewed row."""
    authors = authors_from_str(row["authors"])
    orcids = [o.strip() for o in (row.get("author_orcids") or "").split(";")]
    me = int(row["me_position"]) - 1 if (row.get("me_position") or "").isdigit() else -1
    structs = [s.strip().replace("struct-", "") for s in re.split(r"[;,\s]+", row.get("my_structs", "")) if s.strip()]
    lang = row.get("language") or "en"
    htype = row["hal_type"]

    tei = ET.Element(_t("TEI"))
    bibl = _sub(_sub(_sub(_sub(tei, "text"), "body"), "listBibl"), "biblFull")

    def add_authors(parent):
        for i, a in enumerate(authors):
            _author(parent, a, structs if i == me else [], idhal if i == me else "",
                    orcids[i] if i < len(orcids) else "")

    ts = _sub(bibl, "titleStmt")
    _xml_lang(_sub(ts, "title", row["title"]), lang)
    add_authors(ts)

    if pdf_name:
        ed = _sub(_sub(bibl, "editionStmt"), "edition")
        _sub(ed, "ref", type="file", subtype="author", n="1", target=pdf_name)
        lic = LICENCES.get((row.get("licence") or "").strip().lower(), row.get("licence") or LICENCES["cc by"])
        _sub(_sub(_sub(bibl, "publicationStmt"), "availability"), "licence", target=lic)

    notes = _sub(bibl, "notesStmt")
    _sub(notes, "note", type="audience", n=AUDIENCE.get((row.get("audience") or "").lower(), "1"))
    _sub(notes, "note", type="popular", n="0")
    if htype in ("ART", "COMM", "POSTER", "COUV", "OUV", "DOUV"):
        _sub(notes, "note", type="peer", n=_yes(row.get("peer_reviewed")))
    if htype in ("COMM", "POSTER"):
        _sub(notes, "note", type="invited", n=_yes(row.get("invited")))
        _sub(notes, "note", type="proceedings", n=_yes(row.get("proceedings")))

    bs = _sub(_sub(bibl, "sourceDesc"), "biblStruct")
    an = _sub(bs, "analytic")
    _xml_lang(_sub(an, "title", row["title"]), lang)
    add_authors(an)

    mo = _sub(bs, "monogr")
    if row.get("issn") and htype == "ART":
        _sub(mo, "idno", row["issn"], type="issn")
    if row.get("isbn"):
        _sub(mo, "idno", row["isbn"], type="isbn")
    if htype == "ART":
        _sub(mo, "title", row["venue"], level="j")
    elif htype in ("COUV",) or (htype == "COMM" and row.get("venue") and row["venue"] != row.get("conf_name")):
        _sub(mo, "title", row["venue"], level="m")
    if htype in ("COMM", "POSTER"):
        mt = _sub(mo, "meeting")
        _sub(mt, "title", row["conf_name"])
        _sub(mt, "date", row.get("conf_date") or row["year"], type="start")
        _sub(mt, "settlement", row["conf_city"])
        _sub(mt, "country", key=row["conf_country"].upper())
    if htype in ("THESE", "HDR", "MEM", "REPORT"):
        _sub(mo, "authority", row["institution"], type="institution")
    imp = _sub(mo, "imprint")
    if row.get("publisher"):
        _sub(imp, "publisher", row["publisher"])
    for unit, col in (("volume", "volume"), ("issue", "issue"), ("pp", "pages")):
        if row.get(col):
            _sub(imp, "biblScope", row[col], unit=unit)
    _sub(imp, "date", row["year"], type="dateDefended" if htype in ("THESE", "HDR", "MEM") else "datePub")
    if row.get("doi"):
        _sub(bs, "idno", row["doi"], type="doi")

    pd = _sub(bibl, "profileDesc")
    _sub(_sub(pd, "langUsage"), "language", ident=lang)
    tc = _sub(pd, "textClass")
    for d in re.split(r"[;,\s]+", row.get("domain") or ""):
        if d:
            _sub(tc, "classCode", scheme="halDomain", n=d)
    _sub(tc, "classCode", scheme="halTypology", n=htype)
    if row.get("abstract"):
        _xml_lang(_sub(pd, "abstract", row["abstract"]), lang)

    ET.indent(tei)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tei, encoding="utf-8")


def build_package(row: dict, idhal: str = "") -> tuple[bytes, bool]:
    """TEI alone, or a zip (meta.xml + PDF) when the row points to an existing PDF."""
    pdf = (row.get("pdf") or "").strip().strip('"')
    if not pdf:
        return build_tei(row, idhal), False
    if not os.path.isfile(pdf):
        raise FileNotFoundError(f"PDF not found: {pdf}")
    name = re.sub(r"[^\w.\-]", "_", os.path.basename(pdf))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.xml", build_tei(row, idhal, pdf_name=name))
        z.write(pdf, name)
    return buf.getvalue(), True
