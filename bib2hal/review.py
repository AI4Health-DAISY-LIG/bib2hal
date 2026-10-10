"""The review table: one row per reference, edited by a human before anything is deposited."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .hal import HAL_TYPES
from .util import norm_doi, same_person, similarity

COLUMNS = [
    "action", "missing", "hal_status", "hal_id", "hal_url", "hal_type", "year", "title", "venue", "doi",
    "authors", "me_position", "my_structs", "domain", "language", "audience", "peer_reviewed",
    "proceedings", "invited", "conf_name", "conf_city", "conf_country", "conf_date", "volume", "issue",
    "pages", "publisher", "issn", "isbn", "institution", "pdf", "licence", "abstract", "key", "bib_type",
    "crossref_type", "match", "url", "author_orcids", "validated",
]

ACTIONS = {
    "deposit": "create a new HAL record from this row",
    "attach": "already in HAL but not linked to your IdHAL: link it by hand in HAL (Mes publications)",
    "skip": "nothing to do (already linked, or you decided to leave it out)",
    "duplicate": "same work appears earlier in the file",
    "check": "needs your decision (not sure it is yours, unknown type, weak match...)",
}

REQUIRED = {
    "*": ["title", "year", "authors", "hal_type", "domain", "language", "me_position", "my_structs"],
    "ART": ["venue"],
    "COMM": ["conf_name", "conf_city", "conf_country"],
    "POSTER": ["conf_name", "conf_city", "conf_country"],
    "COUV": ["venue"],
    "OUV": ["publisher"],
    "DOUV": ["publisher"],
    "THESE": ["institution"],
    "HDR": ["institution"],
    "MEM": ["institution"],
    "REPORT": ["institution"],
}


def authors_to_str(authors: list[dict]) -> str:
    return "; ".join(f"{a['family']}, {a['given']}".strip().rstrip(",") for a in authors)


def authors_from_str(s: str) -> list[dict]:
    out = []
    for part in (s or "").split(";"):
        part = part.strip()
        if not part:
            continue
        fam, _, giv = part.partition(",")
        out.append({"family": fam.strip(), "given": giv.strip()})
    return out


def missing_fields(row: dict) -> list[str]:
    t = row.get("hal_type", "")
    miss = [f for f in REQUIRED["*"] + REQUIRED.get(t, []) if not (row.get(f) or "").strip()]
    if t and t not in HAL_TYPES:
        miss.append(f"hal_type '{t}' unknown")
    return miss


def build_row(w: dict, hal_hit: dict, hal_type: str, me: tuple[str, str] | None,
              structs: str, domain: str) -> dict:
    row = {c: "" for c in COLUMNS}
    for k in ("year", "title", "venue", "doi", "conf_name", "conf_city", "conf_country", "conf_date", "volume",
              "issue", "pages", "publisher", "issn", "isbn", "institution", "abstract", "key", "bib_type",
              "crossref_type", "match", "url"):
        row[k] = str(w.get(k, "") or "")
    row["authors"] = authors_to_str(w.get("authors", []))
    row["author_orcids"] = "; ".join(a.get("orcid", "") for a in w.get("authors", []))
    row["hal_type"] = hal_type
    row["hal_status"], row["hal_id"], row["hal_url"] = hal_hit["status"], hal_hit["hal_id"], hal_hit["hal_url"]
    row["domain"], row["language"] = domain, "en"
    row["audience"] = "international"
    row["peer_reviewed"] = "yes" if hal_type in ("ART", "COMM", "POSTER", "COUV") else ""
    row["proceedings"] = "yes" if hal_type == "COMM" and w.get("crossref_type") == "proceedings-article" else (
        "no" if hal_type == "POSTER" else ("yes" if hal_type == "COMM" else ""))
    row["invited"] = "no" if hal_type in ("COMM", "POSTER") else ""
    row["licence"] = "CC BY 4.0"
    if me:
        pos = [i + 1 for i, a in enumerate(w.get("authors", [])) if same_person(a, *me)]
        row["me_position"] = str(pos[0]) if pos else ""
    row["my_structs"] = structs
    return row


def suggest_action(row: dict, seen: list[dict], me_configured: bool) -> str:
    for prev in seen:
        if row["doi"] and norm_doi(prev["doi"]) == norm_doi(row["doi"]):
            return "duplicate"
        if similarity(prev["title"], row["title"]) >= 0.95 and (prev["year"] == row["year"] or not row["year"]):
            return "duplicate"
    st = row["hal_status"]
    if st == "in_hal_linked":
        return "skip"
    if st in ("in_hal", "in_hal_not_linked"):
        return "attach"
    if not row["hal_type"] or (me_configured and not row["me_position"]):
        return "check"
    if row["match"].startswith("no-crossref-match"):
        return "check"
    return "deposit"


def check_reasons(row: dict, me_configured: bool) -> list[dict]:
    """Why a row needs a human look. Each item: {'code', 'msg', 'fields'} (fields to highlight)."""
    out = []
    match = row.get("match", "")
    if me_configured and not row.get("me_position"):
        out.append({"code": "not_me", "fields": ["authors", "me_position"],
                    "msg": "You were not found among the authors: homonym, or your name is spelled differently. "
                           "Skip it if it is not yours, otherwise set your position in the author list."})
    if not row.get("hal_type"):
        out.append({"code": "no_type", "fields": ["hal_type"],
                    "msg": f"Document type unknown (BibTeX '{row.get('bib_type', '')}'): choose the HAL type."})
    if match.startswith("no-crossref-match"):
        best = match.partition("best ")[2].rstrip(")")
        out.append({"code": "no_crossref", "fields": ["title", "venue", "year", "doi"],
                    "msg": "No Crossref match" + (f" (closest title similarity {best})" if best else "") +
                           ": metadata comes only from the BibTeX export. Check title, venue and year, "
                           "and add the DOI if there is one."})
    elif match == "crossref-error":
        out.append({"code": "crossref_error", "fields": ["title", "venue", "year", "doi"],
                    "msg": "Crossref could not be reached: metadata comes only from the BibTeX export."})
    elif match.startswith("crossref:"):
        try:
            sc = float(match.split(":", 1)[1])
        except ValueError:
            sc = 1.0
        if sc < 0.97:
            out.append({"code": "weak_match", "fields": ["title", "venue", "year", "doi", "authors"],
                        "msg": f"Crossref match by title only (similarity {sc:.2f}): make sure it is the same work "
                               "(title, venue, year, authors)."})
    st = row.get("hal_status", "")
    if st in ("not-checked", "hal-error"):
        out.append({"code": "hal_unchecked", "fields": [],
                    "msg": "HAL could not be checked: the work may already be in HAL. Search hal.science "
                           "for the title before depositing."})
    if row.get("action") == "attach" and row.get("hal_url"):
        out.append({"code": "attach", "fields": [],
                    "msg": "Already in HAL but not linked to your IdHAL: open the record and link it from "
                           "'Mes publications' in HAL. Nothing will be deposited."})
    if row.get("action") == "duplicate":
        out.append({"code": "duplicate", "fields": ["title"],
                    "msg": "Same DOI or title as an earlier reference in your file: it will not be deposited twice."})
    return out


def write_csv(rows: list[dict], path: str | Path, delimiter: str = ";") -> None:
    # utf-8-sig + ';' so that Excel (FR locale) opens it correctly with accents.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=delimiter, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)


def read_csv(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delim))
    for r in rows:
        for c in COLUMNS:
            r.setdefault(c, "")
            r[c] = (r[c] or "").strip()
    return rows
