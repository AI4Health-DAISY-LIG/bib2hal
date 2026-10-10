"""The pipeline steps, shared by the command line and the local web UI."""
from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Callable

from . import domains as dom
from .bibtex import parse_bibtex
from .crossref import enrich, from_bibtex
from .hal import find, guess_type, sword_deposit
from .review import build_row, missing_fields, suggest_action
from .tei import build_package
from .util import BlockedError, HttpError, cfg

LOG_COLUMNS = ["date", "target", "key", "title", "ok", "status", "hal_id", "message"]
Progress = Callable[[int, int, dict], None]


def me() -> tuple[str, str] | None:
    m = cfg("BIB2HAL_ME")
    if not m:
        return None
    fam, _, giv = m.partition(",")
    return fam.strip(), giv.strip()


def structs_for(year: str) -> str:
    structs, since = cfg("BIB2HAL_STRUCTS"), cfg("BIB2HAL_STRUCTS_SINCE")
    if not structs:
        return ""
    if since and (not year or not year.isdigit() or int(year) < int(since)):
        return ""
    return structs


def review_bib(text: str, no_crossref: bool = False, no_hal: bool = False, limit: int = 0,
               progress: Progress | None = None) -> list[dict]:
    """BibTeX text -> list of review rows (enriched, checked against HAL, action suggested)."""
    entries = parse_bibtex(text)
    if limit:
        entries = entries[:limit]
    who, idhal = me(), cfg("BIB2HAL_IDHAL")
    shortlist, all_doms = dom.split_codes(cfg("BIB2HAL_DOMAINS")), dom.cached_domains()
    fallback = cfg("BIB2HAL_DOMAIN", "info")
    rows: list[dict] = []
    stop_crossref, stop_hal = no_crossref, no_hal          # switched on if a service starts blocking us
    for i, e in enumerate(entries, 1):
        w = from_bibtex(e)
        note = ""
        if not stop_crossref:
            try:
                w = enrich(w)
            except BlockedError as err:
                stop_crossref, w["match"], note = True, "crossref-error", str(err)
            except HttpError as err:
                w["match"], note = "crossref-error", str(err)
        hit = {"status": "not-checked", "hal_id": "", "hal_url": "", "score": ""}
        if not stop_hal:
            try:
                hit = find(w, idhal)
            except BlockedError as err:
                stop_hal, note = True, str(err)
                hit["status"] = "hal-error"
            except HttpError as err:
                hit["status"], note = "hal-error", str(err)
        domain = dom.suggest(shortlist, all_doms, " ".join([w["title"], w["venue"], w.get("abstract", "")])) \
            or fallback
        row = build_row(w, hit, guess_type(w), who, structs_for(w.get("year", "")), domain)
        row["action"] = suggest_action(row, rows, who is not None)
        if hit["status"] in ("not-checked", "hal-error") and row["action"] == "deposit":
            row["action"] = "check"
        if row["action"] == "deposit":
            row["missing"] = ", ".join(missing_fields(row))
        if note:
            row["missing"] = (row["missing"] + " | " if row["missing"] else "") + note[:200]
        rows.append(row)
        if progress:
            progress(i, len(entries), row)
    return rows


def row_problems(r: dict, all_doms: list[dict] | None = None) -> list[str]:
    """Everything that would block the deposit of this row."""
    all_doms = dom.cached_domains() if all_doms is None else all_doms
    miss = missing_fields(r)
    miss += [f"unknown domain '{c}'" for c in dom.unknown_codes(r.get("domain", ""), all_doms)]
    if r.get("pdf") and not os.path.isfile(r["pdf"].strip('"')):
        miss.append(f"pdf not found ({r['pdf']})")
    return miss


def split_ready(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, list[str]]]]:
    all_doms = dom.cached_domains()
    ready, blocked = [], []
    for r in rows:
        if r["action"].lower() != "deposit":
            continue
        miss = row_problems(r, all_doms)
        (blocked.append((r, miss)) if miss else ready.append(r))
    return ready, blocked


DONE_TARGETS = ("hal", "prod")          # 'prod' = logs written by bib2hal 0.1


def already_done(log: Path) -> set[str]:
    """Keys already deposited successfully in HAL (tests excluded)."""
    if not log.exists():
        return set()
    with open(log, encoding="utf-8-sig", newline="") as f:
        return {r["key"] for r in csv.DictReader(f, delimiter=";") if r["target"] in DONE_TARGETS and r["ok"] == "True"}


def read_log(log: Path) -> list[dict]:
    if not log.exists():
        return []
    with open(log, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def deposit(rows: list[dict], login: str, password: str, log: Path,
            progress: Progress | None = None, deposit_fn=None, test: bool = False) -> list[dict]:
    """Deposit ready rows not yet in HAL; append each result to the log.

    test=True: HAL validates each deposit completely but creates nothing (logged as 'hal-test', so a later
    real deposit is not skipped).
    """
    deposit_fn = deposit_fn or sword_deposit
    log_target = "hal-test" if test else "hal"
    done = set() if test else already_done(log)
    todo = [r for r in rows if r["key"] not in done]
    idhal = cfg("BIB2HAL_IDHAL")
    new_log = not log.exists()
    results = []
    with open(log, "a", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
        if new_log:
            wr.writeheader()
        for i, r in enumerate(todo, 1):
            blocked = False
            try:
                payload, is_zip = build_package(r, idhal)
                res = deposit_fn(payload, is_zip, login, password, test=test)
            except BlockedError as e:
                res, blocked = {"ok": False, "status": 0, "hal_id": "", "message": str(e)}, True
            except (OSError, HttpError) as e:
                res = {"ok": False, "status": 0, "hal_id": "", "message": str(e)}
            entry = {"date": dt.datetime.now().isoformat(timespec="seconds"), "target": log_target, "key": r["key"],
                     "title": r["title"][:120], "ok": res["ok"], "status": res["status"],
                     "hal_id": res["hal_id"], "message": (res.get("message") or "")[:500]}
            wr.writerow(entry)
            f.flush()
            results.append(entry)
            if progress:
                progress(i, len(todo), entry)
            if blocked:                                      # never insist against a protection page
                break
    return results
