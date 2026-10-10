"""Turn BibTeX entries into normalised work records and enrich them from Crossref."""
from __future__ import annotations

import re
import urllib.parse

from .bibtex import latex_to_unicode, split_authors
from .util import get_json, norm_doi, similarity

CROSSREF = "https://api.crossref.org/works"

# Minimal country-name -> ISO 3166-1 alpha-2 table (HAL wants the code for conferences).
COUNTRIES = {
    "france": "FR", "usa": "US", "united states": "US", "united states of america": "US", "us": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "germany": "DE", "italy": "IT", "spain": "ES", "portugal": "PT", "switzerland": "CH",
    "belgium": "BE", "netherlands": "NL", "the netherlands": "NL", "austria": "AT", "sweden": "SE",
    "norway": "NO", "denmark": "DK", "finland": "FI", "ireland": "IE", "poland": "PL", "greece": "GR",
    "czech republic": "CZ", "czechia": "CZ", "hungary": "HU", "luxembourg": "LU", "japan": "JP",
    "china": "CN", "taiwan": "TW", "korea": "KR", "south korea": "KR", "singapore": "SG",
    "india": "IN", "australia": "AU", "new zealand": "NZ", "brazil": "BR", "mexico": "MX",
    "israel": "IL", "turkey": "TR", "türkiye": "TR", "south africa": "ZA", "morocco": "MA",
    "tunisia": "TN", "egypt": "EG", "hong kong": "HK", "vietnam": "VN", "thailand": "TH",
    "united arab emirates": "AE", "uae": "AE", "qatar": "QA", "chile": "CL", "argentina": "AR",
}

US_STATES = {"ca", "ny", "ma", "wa", "tx", "il", "pa", "fl", "co", "md", "nc", "ga", "dc", "or", "mn", "hi", "louisiana",
             "california", "new york", "massachusetts", "washington", "texas", "hawaii"}


def country_code(name: str) -> str:
    n = (name or "").strip().lower().rstrip(".")
    if len(n) == 2 and n.isalpha() and n not in US_STATES:
        return n.upper()
    if n in US_STATES:
        return "US"
    return COUNTRIES.get(n, "")


def split_location(loc: str) -> tuple[str, str]:
    """'Grenoble, France' -> ('Grenoble', 'FR'); unknown country -> ('Grenoble', '')."""
    bits = [b.strip() for b in (loc or "").split(",") if b.strip()]
    if not bits:
        return "", ""
    if len(bits) == 1:
        cc = country_code(bits[0])
        return ("", cc) if cc else (bits[0], "")
    return bits[0], country_code(bits[-1])


def _year(s: str) -> str:
    m = re.search(r"(19|20)\d{2}", s or "")
    return m.group(0) if m else ""


def from_bibtex(e: dict) -> dict:
    g = lambda k: latex_to_unicode(e.get(k, ""))  # noqa: E731
    venue = g("journal") or g("booktitle") or g("series") or g("howpublished")
    w = {
        "key": e.get("ID", ""),
        "bib_type": e.get("ENTRYTYPE", ""),
        "title": g("title"),
        "year": _year(e.get("year", "") or e.get("date", "")),
        "doi": norm_doi(e.get("doi", "")),
        "authors": split_authors(e.get("author", "")),
        "venue": venue,
        "volume": g("volume"),
        "issue": g("number") or g("issue"),
        "pages": g("pages").replace("--", "-").replace("–", "-"),
        "publisher": g("publisher") or g("organization"),
        "issn": g("issn"),
        "isbn": g("isbn"),
        "institution": g("school") or g("institution"),
        "conf_name": g("booktitle") if e.get("ENTRYTYPE") in ("inproceedings", "conference") else "",
        "conf_city": "", "conf_country": "", "conf_date": "",
        "abstract": g("abstract"),
        "url": e.get("url", ""),
        "crossref_type": "",
        "match": "",
    }
    if g("address"):
        w["conf_city"], w["conf_country"] = split_location(g("address"))
    if not w["doi"]:
        m = re.search(r"10\.\d{4,9}/\S+", e.get("url", "") or "")
        if m:
            w["doi"] = norm_doi(m.group(0).rstrip(".,;"))
    return w


def _cr_date(item: dict) -> str:
    for k in ("published-print", "published-online", "issued", "published"):
        parts = (item.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def _merge_crossref(w: dict, it: dict, score: float) -> dict:
    def first(k):
        v = it.get(k) or []
        return (v[0] if isinstance(v, list) and v else v) or ""

    w = dict(w)
    w["doi"] = norm_doi(it.get("DOI", "")) or w["doi"]
    w["crossref_type"] = it.get("type", "")
    w["title"] = re.sub(r"\s+", " ", first("title") or w["title"]).strip()
    w["year"] = _cr_date(it) or w["year"]
    authors = [
        {"family": a.get("family", ""), "given": a.get("given", ""),
         "orcid": (a.get("ORCID") or "").rsplit("/", 1)[-1]}
        for a in it.get("author", []) if a.get("family")
    ]
    if authors:
        w["authors"] = authors
    w["venue"] = first("container-title") or w["venue"]
    for k_w, k_c in (("volume", "volume"), ("issue", "issue"), ("pages", "page"), ("publisher", "publisher")):
        w[k_w] = it.get(k_c) or w[k_w]
    w["issn"] = first("ISSN") or w["issn"]
    w["isbn"] = first("ISBN") or w["isbn"]
    ev = it.get("event") or {}
    if ev:
        w["conf_name"] = ev.get("name") or w["conf_name"]
        if ev.get("location"):
            w["conf_city"], w["conf_country"] = split_location(ev["location"])
        st = (ev.get("start") or {}).get("date-parts") or [[]]
        if st and st[0]:
            w["conf_date"] = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(st[0]))
    if it.get("abstract") and not w["abstract"]:
        w["abstract"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", it["abstract"])).strip()
    w["match"] = f"crossref:{score:.2f}"
    return w


def enrich(w: dict, min_score: float = 0.92) -> dict:
    """Return w completed with Crossref metadata (by DOI, else by title search)."""
    if w["doi"]:
        data = get_json(f"{CROSSREF}/{urllib.parse.quote(w['doi'], safe='/:;()')}")
        if data and data.get("message"):
            return _merge_crossref(w, data["message"], 1.0)
    if not w["title"]:
        return w
    params = {"query.bibliographic": w["title"], "rows": 5,
              "select": "DOI,title,type,author,container-title,issued,published-print,published-online,"
                        "volume,issue,page,publisher,ISSN,ISBN,event,abstract"}
    if w["authors"]:
        params["query.author"] = w["authors"][0]["family"]
    data = get_json(CROSSREF, params) or {}
    best, best_s = None, 0.0
    for it in (data.get("message") or {}).get("items", []):
        s = similarity(w["title"], (it.get("title") or [""])[0])
        y = _cr_date(it)
        if w["year"] and y and abs(int(y) - int(w["year"])) > 1:
            s -= 0.2
        if s > best_s:
            best, best_s = it, s
    if best is not None and best_s >= min_score:
        return _merge_crossref(w, best, best_s)
    w = dict(w)
    w["match"] = f"no-crossref-match (best {best_s:.2f})" if best is not None else "no-crossref-match"
    return w
