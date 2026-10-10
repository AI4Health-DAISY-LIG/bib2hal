"""Command-line interface.

    bib2hal review  scholar.bib -o review.csv   # enrich + check HAL, nothing is deposited
    bib2hal tei     review.csv  -o tei/          # write the TEI files for rows marked 'deposit'
    bib2hal login                                # sign in to HAL once (remembered in the OS keychain)
    bib2hal deposit review.csv --test            # HAL validates everything, creates nothing
    bib2hal deposit review.csv                   # submit rows marked 'deposit' to HAL, after confirmation
    bib2hal struct  "Laboratoire d'informatique de Grenoble"
    bib2hal domains --pick neuro               # choose your HAL disciplines (shortlist saved in .env)
    bib2hal validate review.csv                # go through each paper: action, type, discipline
    bib2hal config                               # show what is configured
    bib2hal ui                                   # local web interface for the whole pipeline
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from . import domains as dom
from . import credentials as cred
from . import pipeline as pl
from .hal import HAL_TYPES, check_login, search_structures, sword_deposit
from .review import ACTIONS, COLUMNS, check_reasons, missing_fields, read_csv, write_csv
from .tei import build_tei
from .util import CONFIG_KEYS, HttpError, cfg, load_env


# --------------------------------------------------------------------------- review

def cmd_review(a) -> int:
    bib = Path(a.bibfile)
    if not bib.is_file():
        print(f"File not found: {bib}\n(current folder: {Path.cwd()})\n"
              "Run the command from the folder that contains your .bib, or give its full path.", file=sys.stderr)
        return 2
    text = bib.read_text(encoding="utf-8", errors="replace")

    def show(i, n, row):
        if i == 1:
            print(f"{n} references read from {a.bibfile}")
        print(f"  [{i:>3}/{n}] {row['action']:<9} {row['hal_type'] or '?':<9} {row['year'] or '----'}  "
              f"{row['title'][:70]}")

    rows = pl.review_bib(text, a.no_crossref, a.no_hal, a.limit, progress=show)
    write_csv(rows, a.output)
    c = Counter(r["action"] for r in rows)
    print(f"\nWrote {a.output}")
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {k:<9} {v:>4}   {ACTIONS.get(k, '')}")
    print(f"\nNext:  bib2hal ui   (web interface)   or   bib2hal validate {a.output}   (terminal);\n"
          f"then:  bib2hal tei {a.output}   (preview)   and   bib2hal deposit {a.output} --test   (check with HAL).")
    return 0


# --------------------------------------------------------------------------- tei

def _report_blocked(blocked) -> None:
    if blocked:
        print(f"\n{len(blocked)} row(s) marked 'deposit' are incomplete and were skipped:")
        for r, miss in blocked:
            print(f"  - {r['key'] or r['title'][:40]}: missing {', '.join(miss)}")


def cmd_tei(a) -> int:
    ready, blocked = pl.split_ready(read_csv(a.review))
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    idhal = cfg("BIB2HAL_IDHAL")
    for n, r in enumerate(ready):
        name = re.sub(r"[^\w.\-]", "_", r["key"] or f"row{n}")
        (out / f"{name}.xml").write_bytes(build_tei(r, idhal, pdf_name=os.path.basename(r["pdf"]) if r["pdf"] else ""))
    print(f"{len(ready)} TEI file(s) written to {out}/")
    _report_blocked(blocked)
    return 0


# --------------------------------------------------------------------------- deposit

def _ask_login() -> tuple[str, str] | None:
    """Prompt for HAL credentials (password hidden) and check them with HAL."""
    import getpass
    login = input("HAL login: ").strip()
    pwd = getpass.getpass(f"HAL password for {login}: ")
    res = check_login(login, pwd)
    if not res["ok"]:
        print(res["message"], file=sys.stderr)
        return None
    return login, pwd


def cmd_deposit(a) -> int:
    ready, blocked = pl.split_ready(read_csv(a.review))
    log = Path(a.log)
    done = set() if a.test else pl.already_done(log)
    todo = [r for r in ready if r["key"] not in done]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{'CHECK ONLY — HAL validates, nothing is created.  ' if a.test else ''}"
          f"{len(todo)} to send ({len(ready) - len(todo)} already in HAL)")
    _report_blocked(blocked)
    if not todo:
        return 0
    c = cred.get()
    if not c:
        print("Not signed in to HAL (run `bib2hal login` to be remembered on this computer).")
        c = _ask_login()
        if not c:
            return 2
    login, pwd = c
    if not a.yes and not a.test:
        ans = input(f"\nSubmit {len(todo)} record(s) to HAL under the account '{login}'? "
                    "They will be public once HAL moderators accept them. Type 'yes' to continue: ")
        if ans.strip().lower() != "yes":
            print("Aborted.")
            return 1

    def show(_i, _n, e):
        print(f"  {'OK ' if e['ok'] else 'ERR'} {e['status']} {e['hal_id']:<20} {e['title'][:60]}"
              + ("" if e["ok"] else f"\n      {e['message'][:300]}"))

    pl.deposit(todo, login, pwd, log, progress=show,
               deposit_fn=lambda *args, **kw: sword_deposit(*args, **kw), test=a.test)
    print(f"\nLog: {log}")
    return 0


# --------------------------------------------------------------------------- domains / validate

def _print_domains(lst: list[dict], numbered: bool = False) -> None:
    for i, d in enumerate(lst, 1):
        lab = d["label_en"] or d["label_fr"]
        print(f"  {str(i) + '.' if numbered else '':>5} {d['code']:<22} {lab}")


def _parse_numbers(ans: str, n: int) -> list[int] | None:
    try:
        idx = [int(x) for x in re.split(r"[,\s]+", ans.strip()) if x]
    except ValueError:
        return None
    return idx if idx and all(1 <= i <= n for i in idx) else None


def cmd_domains(a) -> int:
    all_doms = dom.load_domains(refresh=a.refresh)
    if not all_doms:
        print("Could not get the HAL domain list.", file=sys.stderr)
        return 3
    terms = " ".join(a.terms)
    if not a.pick:
        res = dom.search(all_doms, terms)
        _print_domains(res)
        print(f"\n{len(res)} / {len(all_doms)} domains. Pick yours with:  bib2hal domains --pick [words]")
        return 0
    chosen = dom.split_codes(cfg("BIB2HAL_DOMAINS"))
    print("Build your shortlist of HAL disciplines (used to suggest one per paper).")
    while True:
        print("\nCurrent shortlist:", ", ".join(f"{c} ({dom.label(all_doms, c)})" for c in chosen) or "(empty)")
        if not terms:
            terms = input("Search words (e.g. 'neuro', 'intelligence artificielle', 'bioinfo'), "
                          "Enter to finish: ").strip()
            if not terms:
                break
        res = dom.search(all_doms, terms)[:60]
        terms = ""
        if not res:
            print("  nothing found")
            continue
        _print_domains(res, numbered=True)
        ans = input("Numbers to add (e.g. 1,3), Enter to skip: ").strip()
        idx = _parse_numbers(ans, len(res)) if ans else []
        if idx is None:
            print("  invalid input")
            continue
        for i in idx:
            if res[i - 1]["code"] not in chosen:
                chosen.append(res[i - 1]["code"])
    if not chosen:
        print("Nothing chosen, .env unchanged.")
        return 0
    dom.write_env_value("BIB2HAL_DOMAINS", ";".join(chosen))
    print(f"Saved BIB2HAL_DOMAINS={';'.join(chosen)} in .env "
          f"(the first one is the default when no better match is found).")
    return 0


def cmd_validate(a) -> int:
    rows = read_csv(a.review)
    try:
        all_doms = dom.cached_domains() or dom.load_domains()
    except HttpError:
        all_doms = []
        print("(HAL domain list unavailable: labels and search are disabled)")
    shortlist = dom.split_codes(cfg("BIB2HAL_DOMAINS"))
    wanted = {x.strip() for x in a.actions.split(",")}
    todo = [r for r in rows if r["action"] in wanted and (a.all or r.get("validated") != "yes")]
    print(f"{len(todo)} paper(s) to validate (actions: {', '.join(sorted(wanted))}). "
          "Answers are saved after each paper; 'q' to stop and resume later.\n")
    for n, r in enumerate(todo, 1):
        while True:
            r["missing"] = ", ".join(missing_fields(r)) if r["action"] == "deposit" else r["missing"]
            print("-" * 78)
            print(f"[{n}/{len(todo)}] {r['year']}  {r['title']}")
            print(f"      {r['venue'] or r['conf_name'] or '-'}   |   {r['authors'][:90]}")
            print(f"      action={r['action']}   type={r['hal_type'] or '?'} "
                  f"({HAL_TYPES.get(r['hal_type'], '?')})   hal={r['hal_status']} {r['hal_id']}")
            shown = ", ".join(f"{c} ({dom.label(all_doms, c)})" for c in dom.split_codes(r["domain"]))
            print(f"      domain: {shown or '(none)'}")
            if r["missing"]:
                print(f"      missing: {r['missing']}")
            for rs in check_reasons(r, bool(cfg("BIB2HAL_ME"))):
                print(f"      CHECK: {rs['msg']}")
            for i, c in enumerate(shortlist, 1):
                print(f"        {i}. {c:<20} {dom.label(all_doms, c)}")
            ans = input("  Enter=OK | 1,2=domains | s WORDS=search domains | t TYPE | a ACTION | "
                        "e field=value | q=quit > ").strip()
            if ans == "":
                r["validated"] = "yes"
                break
            if ans.lower() == "q":
                write_csv(rows, a.review)
                print(f"Saved {a.review}. Run the same command to resume.")
                return 0
            if ans[:2].lower() == "s ":
                res = dom.search(all_doms, ans[2:])[:40]
                _print_domains(res, numbered=True)
                pick = input("  Numbers to use for this paper (Enter to cancel): ").strip()
                idx = _parse_numbers(pick, len(res)) if pick else None
                if idx:
                    r["domain"] = ";".join(res[i - 1]["code"] for i in idx)
                continue
            if ans[:2].lower() == "t ":
                t = ans[2:].strip().upper()
                if t in HAL_TYPES:
                    r["hal_type"] = t
                else:
                    print(f"  unknown type; choose among {', '.join(HAL_TYPES)}")
                continue
            if ans[:2].lower() == "a ":
                act = ans[2:].strip().lower()
                if act in ACTIONS:
                    r["action"] = act
                else:
                    print(f"  unknown action; choose among {', '.join(ACTIONS)}")
                continue
            if ans[:2].lower() == "e " and "=" in ans:
                field, _, value = ans[2:].partition("=")
                field = field.strip()
                if field in COLUMNS and field not in ("validated",):
                    r[field] = value.strip()
                else:
                    print(f"  unknown field '{field}' (see the CSV header)")
                continue
            idx = _parse_numbers(ans, len(shortlist))
            if idx:
                r["domain"] = ";".join(shortlist[i - 1] for i in idx)
            else:
                print("  not understood")
        write_csv(rows, a.review)
    print(f"\nDone. Saved {a.review}.")
    return 0


# --------------------------------------------------------------------------- login / logout

def cmd_login(a) -> int:
    import getpass
    where = cred.keychain_label()
    if not where:
        print("No system keychain is available on this computer, so bib2hal cannot remember you.\n"
              "You will simply be asked for your HAL password when you deposit (it is never written to a file).\n"
              "Tip: `pip install keyring` adds keychain support.", file=sys.stderr)
        return 1
    login = a.user or input("HAL login: ").strip()
    pwd = getpass.getpass(f"HAL password for {login}: ")
    res = check_login(login, pwd)
    if not res["ok"]:
        print(res["message"], file=sys.stderr)
        return 1
    if not cred.remember(login, pwd):
        print(f"HAL accepted the login, but saving it to the {where} failed.", file=sys.stderr)
        return 1
    print(f"Signed in to HAL as {login}; remembered in the {where}.")
    return 0


def cmd_logout(_a) -> int:
    cred.forget()
    print("Signed out of HAL (removed from this computer).")
    return 0


def cmd_whoami(_a) -> int:
    c = cred.get()
    print(f"Signed in to HAL as {c[0]}" if c else "Not signed in to HAL.")
    print(f"Keychain: {cred.keychain_label() or 'none available'}")
    return 0


# --------------------------------------------------------------------------- struct / config

def cmd_struct(a) -> int:
    docs = search_structures(a.query)
    if not docs:
        print("No structure found.")
        return 1
    print(f"{'docid':>8}  {'status':<9} {'type':<14} acronym — name")
    for d in docs:
        print(f"{d.get('docid', ''):>8}  {d.get('valid_s', ''):<9} {d.get('type_s', ''):<14} "
              f"{d.get('acronym_s', '')} — {d.get('name_s', '')}")
    print("\nUse VALID structures. Put the docid(s) in BIB2HAL_STRUCTS (e.g. BIB2HAL_STRUCTS=12345;67890).")
    return 0


def cmd_config(_a) -> int:
    for k, desc in CONFIG_KEYS.items():
        v = cfg(k)
        shown = ("*" * 8 if v else "") if "PASSWORD" in k else v
        print(f"{k:<22} {shown or '(not set)':<30} {desc}")
    print("\nHAL document types:", ", ".join(f"{k}={v}" for k, v in HAL_TYPES.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env(".env")
    p = argparse.ArgumentParser(prog="bib2hal", description="Bring a BibTeX publication list into HAL, with review.")
    p.add_argument("--version", action="version", version=f"bib2hal {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="BibTeX -> enriched review CSV (no deposit)")
    r.add_argument("bibfile")
    r.add_argument("-o", "--output", default="review.csv")
    r.add_argument("--no-crossref", action="store_true", help="do not query Crossref")
    r.add_argument("--no-hal", action="store_true", help="do not check HAL for existing records")
    r.add_argument("--limit", type=int, default=0, help="only process the first N references (testing)")
    r.set_defaults(func=cmd_review)

    t = sub.add_parser("tei", help="write TEI files for rows marked 'deposit' (preview, no network)")
    t.add_argument("review")
    t.add_argument("-o", "--output", default="tei")
    t.set_defaults(func=cmd_tei)

    d = sub.add_parser("deposit", help="submit rows marked 'deposit' to HAL")
    d.add_argument("review")
    d.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    d.add_argument("--limit", type=int, default=0, help="deposit at most N rows")
    d.add_argument("--log", default="deposit_log.csv")
    d.add_argument("--test", action="store_true", help="HAL checks everything but creates nothing (X-test)")
    d.set_defaults(func=cmd_deposit)

    s = sub.add_parser("struct", help="find HAL structure ids (labs, universities)")
    s.add_argument("query")
    s.set_defaults(func=cmd_struct)

    dm = sub.add_parser("domains", help="list/search HAL disciplines; --pick to choose your shortlist")
    dm.add_argument("terms", nargs="*", help="search words (code or label, FR/EN)")
    dm.add_argument("--pick", action="store_true", help="interactively choose your shortlist (saved in .env)")
    dm.add_argument("--refresh", action="store_true", help="re-download the list from HAL")
    dm.set_defaults(func=cmd_domains)

    v = sub.add_parser("validate", help="go through each paper and confirm action, type and discipline")
    v.add_argument("review")
    v.add_argument("--actions", default="deposit,check", help="which rows to go through (default: deposit,check)")
    v.add_argument("--all", action="store_true", help="also revisit rows already validated")
    v.set_defaults(func=cmd_validate)

    li = sub.add_parser("login", help="sign in to HAL (checked with HAL, remembered in the OS keychain)")
    li.add_argument("--user", default="", help="HAL login (asked if omitted)")
    li.set_defaults(func=cmd_login)
    lo = sub.add_parser("logout", help="forget HAL credentials")
    lo.set_defaults(func=cmd_logout)
    sub.add_parser("whoami", help="show HAL sign-in status").set_defaults(func=cmd_whoami)

    u = sub.add_parser("ui", help="open the local web interface (whole pipeline, no command line)")
    u.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a free one)")
    u.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    u.set_defaults(func=lambda a: __import__("bib2hal.ui", fromlist=["run"]).run(a.port, not a.no_browser))

    c = sub.add_parser("config", help="show configuration")
    c.set_defaults(func=cmd_config)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except HttpError as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 3
