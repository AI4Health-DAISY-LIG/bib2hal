"""HAL APIs: search (duplicate check), structure referential, SWORD deposit."""
from __future__ import annotations

import base64
import os
import re
import xml.etree.ElementTree as ET

from .util import BlockedError, get_json, norm_doi, norm_title, request, similarity

SEARCH = "https://api.archives-ouvertes.fr/search/"
REF_STRUCT = "https://api.archives-ouvertes.fr/ref/structure/"
# HAL deposit API (SWORD). Developers can point it at HAL's test server with the BIB2HAL_SWORD_URL
# environment variable (e.g. https://api-preprod.archives-ouvertes.fr/sword/hal/); users never need to.
SWORD_DEFAULT = "https://api.archives-ouvertes.fr/sword/hal/"


def sword_url() -> str:
    return os.environ.get("BIB2HAL_SWORD_URL", "").strip() or SWORD_DEFAULT
FIELDS = "halId_s,uri_s,docType_s,title_s,doiId_s,authIdHal_s,producedDateY_i"

HAL_TYPES = {
    "ART": "Journal article", "COMM": "Conference paper", "POSTER": "Poster",
    "COUV": "Book chapter", "OUV": "Book", "DOUV": "Edited book", "REPORT": "Report",
    "THESE": "PhD thesis", "HDR": "HDR", "MEM": "Master thesis", "UNDEFINED": "Preprint / working paper",
    "OTHER": "Other",
}

PREPRINT_SERVERS = re.compile(r"arxiv|biorxiv|medrxiv|chemrxiv|psyarxiv|ssrn|research square|preprints?\.org|hal\b", re.I)


def guess_type(w: dict) -> str:
    cr = w.get("crossref_type", "")
    venue = w.get("venue", "")
    if cr:
        m = {"journal-article": "ART", "proceedings-article": "COMM", "book-chapter": "COUV",
             "book": "OUV", "monograph": "OUV", "edited-book": "DOUV", "posted-content": "UNDEFINED",
             "report": "REPORT", "dissertation": "THESE"}.get(cr)
        if m:
            return m
    bt = w.get("bib_type", "")
    if PREPRINT_SERVERS.search(venue or "") and bt in ("article", "misc", "unpublished", ""):
        return "UNDEFINED"
    return {"article": "ART", "inproceedings": "COMM", "conference": "COMM", "incollection": "COUV",
            "inbook": "COUV", "book": "OUV", "phdthesis": "THESE", "mastersthesis": "MEM",
            "techreport": "REPORT", "unpublished": "UNDEFINED"}.get(bt, "")


def _lucene_words(title: str, n: int = 12) -> str:
    words = [x for x in norm_title(title).split() if len(x) > 2][:n]
    return " AND ".join(words)


def find(w: dict, idhal: str = "", min_score: float = 0.9) -> dict:
    """Look for w in HAL. Returns {'status', 'hal_id', 'hal_url', 'score'}.

    status: 'absent' | 'in_hal' (idhal unknown) | 'in_hal_linked' | 'in_hal_not_linked'
    """
    hits = []
    if w.get("doi"):
        d = get_json(SEARCH, {"q": f'doiId_s:"{norm_doi(w["doi"])}"', "fl": FIELDS, "rows": 5, "wt": "json"}) or {}
        hits = [(1.0, h) for h in d.get("response", {}).get("docs", [])]
    if not hits and w.get("title"):
        q = _lucene_words(w["title"])
        if q:
            d = get_json(SEARCH, {"q": f"title_t:({q})", "fl": FIELDS, "rows": 10, "wt": "json"}) or {}
            for h in d.get("response", {}).get("docs", []):
                s = max((similarity(w["title"], t) for t in h.get("title_s", [])), default=0.0)
                if s >= min_score:
                    hits.append((s, h))
    if not hits:
        return {"status": "absent", "hal_id": "", "hal_url": "", "score": ""}
    hits.sort(key=lambda x: -x[0])
    if idhal:
        for s, h in hits:
            if idhal in (h.get("authIdHal_s") or []):
                return {"status": "in_hal_linked", "hal_id": h["halId_s"], "hal_url": h.get("uri_s", ""), "score": f"{s:.2f}"}
    s, h = hits[0]
    return {"status": "in_hal_not_linked" if idhal else "in_hal", "hal_id": h["halId_s"],
            "hal_url": h.get("uri_s", ""), "score": f"{s:.2f}"}


def search_structures(query: str, rows: int = 25) -> list[dict]:
    """Search AuréHAL structures (labs, universities...) by name or acronym."""
    words = " AND ".join(w for w in norm_title(query).split())
    if not words:
        return []
    d = get_json(REF_STRUCT, {"q": f"text:({words})", "fl": "docid,acronym_s,name_s,type_s,valid_s,country_s",
                              "rows": rows, "wt": "json", "sort": "valid_s desc"}) or {}
    return d.get("response", {}).get("docs", [])


def _basic(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def check_login(login: str, password: str) -> dict:
    """Ask HAL whether these credentials are valid (reads the SWORD service document; creates nothing).

    Returns {'ok': bool, 'status': int, 'message': str}.
    """
    url = sword_url().replace("/sword/hal/", "/sword/servicedocument")
    try:
        status, body = request(url, headers={"Authorization": _basic(login, password)})
    except BlockedError as e:
        return {"ok": False, "status": 0, "message": str(e)}
    if status == 200:
        return {"ok": True, "status": status, "message": ""}
    if status in (401, 403):
        return {"ok": False, "status": status, "message": "HAL refused this login or password."}
    if status == 0:
        return {"ok": False, "status": 0, "message": f"HAL could not be reached: {body[:200]}"}
    return {"ok": False, "status": status, "message": f"Unexpected answer from HAL ({status})."}


def sword_deposit(payload: bytes, is_zip: bool, login: str, password: str,
                  filename: str = "meta.xml", test: bool = False) -> dict:
    """POST one TEI (or zip of TEI + PDF) to HAL SWORD. Returns {'ok', 'status', 'hal_id', 'message', 'raw'}.

    test=True sends 'X-test: 1': HAL validates the deposit completely but creates nothing.
    """
    headers = {
        "Authorization": _basic(login, password),
        "Packaging": "http://purl.org/net/sword-types/AOfr",
        "Content-Type": "application/zip" if is_zip else "text/xml",
        "Content-Disposition": f"attachment; filename={filename}",
    }
    if test:
        headers["X-test"] = "1"
    status, body = request(sword_url(), payload, headers, method="POST")
    hal_id, msg = "", ""
    try:
        root = ET.fromstring(body)
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "id" and el.text and not hal_id:
                hal_id = el.text.strip()
            if tag in ("summary", "verboseDescription", "title") and el.text and status >= 300:
                msg += el.text.strip() + " "
    except ET.ParseError:
        msg = body[:500]
    return {"ok": 200 <= status < 300, "status": status, "hal_id": hal_id, "message": msg.strip(), "raw": body}
