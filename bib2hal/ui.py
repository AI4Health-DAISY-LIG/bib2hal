"""Local web interface: `bib2hal ui` serves a single page on 127.0.0.1 and opens it in your browser.

Standard library only. The server listens on the loopback interface only, every API call must carry a
random per-session token, and the Host header is checked, so other websites open in your browser cannot
drive it. Everything stays in the folder you start it from (.env, review.csv, deposit_log.csv).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import threading
import webbrowser
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import domains as dom
from . import credentials as cred
from . import pipeline as pl
from .bibtex import write_bibtex
from .errors import explain
from .hal import HAL_TYPES, check_login, search_structures, structure_names, sword_deposit
from .review import ACTIONS, COLUMNS, check_reasons, missing_fields, read_csv, write_csv
from .tei import build_tei
from .util import CONFIG_KEYS, HttpError, cfg

STATIC = Path(__file__).parent / "static"
HIDDEN_KEYS = {"HAL_LOGIN", "HAL_PASSWORD"}     # never shown or saved: HAL credentials go through the sign-in panel
MAX_UPLOAD = 60 * 1024 * 1024          # BibTeX files and PDFs


class App:
    """State shared by all requests (one user, one working folder)."""

    def __init__(self, workdir: Path, review_name: str = "review.csv", log_name: str = "deposit_log.csv",
                 deposit_fn=None, check_fn=None):
        self.workdir = workdir
        self.review_path = workdir / review_name
        self.log_path = workdir / log_name
        self.env_path = workdir / ".env"
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.RLock()
        self.rows: list[dict] = read_csv(self.review_path) if self.review_path.exists() else []
        self.job: dict = {"kind": "", "running": False, "i": 0, "n": 0, "lines": [], "error": "", "result": None}
        self.deposit_fn = deposit_fn or sword_deposit
        self.check_fn = check_fn or check_login
        self.struct_names: dict[str, str] = {}

    def auth_state(self) -> dict:
        c = cred.get()
        return {"keychain": cred.keychain_label(), "signed_in": bool(c), "login": c[0] if c else "",
                "source": cred.source() if c else "",
                # a password left in .env by an older version: never used, but we offer to delete it
                "env_password": bool(cfg("HAL_PASSWORD"))}

    # ------------------------------------------------------------------ helpers
    def save_rows(self) -> None:
        write_csv(self.rows, self.review_path)

    def row_view(self, i: int, r: dict, all_doms, attempts: dict | None = None) -> dict:
        v = dict(r)
        v["id"] = i
        v["problems"] = pl.row_problems(r, all_doms) if r["action"] == "deposit" else []
        v["reasons"] = check_reasons(r, bool(cfg("BIB2HAL_ME")))
        attempts = pl.last_attempts(self.log_path) if attempts is None else attempts
        e = attempts.get(r["key"])
        v["last_error"] = None
        if e and e.get("ok") != "True":
            v["last_error"] = {"date": e["date"], "what": "check" if e["target"].endswith("-test") else "submission",
                               "status": e["status"], "message": e["message"], **explain(e["message"], e["status"])}
        return v

    def start_job(self, kind: str, fn) -> bool:
        with self.lock:
            if self.job["running"]:
                return False
            self.job = {"kind": kind, "running": True, "i": 0, "n": 0, "lines": [], "error": "", "result": None}

        def run():
            try:
                self.job["result"] = fn()
            except Exception as e:  # noqa: BLE001 — reported to the page
                self.job["error"] = f"{type(e).__name__}: {e}"
            finally:
                self.job["running"] = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def progress(self, i: int, n: int, line: str) -> None:
        self.job["i"], self.job["n"] = i, n
        self.job["lines"] = (self.job["lines"] + [line])[-400:]


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"bib2hal/{__version__}"

        def log_message(self, *_):  # keep the terminal quiet
            pass

        # ------------------------------------------------------------- plumbing
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _err(self, msg: str, code: int = 400) -> None:
            self._json({"error": msg}, code)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _auth(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Bib2hal-Token", ""), app.token)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                raise ValueError("upload too large")
            return self.rfile.read(n) if n else b""

        def _jbody(self) -> dict:
            b = self._body()
            return json.loads(b.decode("utf-8")) if b else {}

        def _route(self, method: str):
            if not self._host_ok():
                return self._err("bad host", 403)
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            path = url.path
            if method == "GET" and path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if not path.startswith("/api/"):
                return self._err("not found", 404)
            if not self._auth():
                return self._err("missing or wrong token", 403)
            name = f"{method}_{path[5:].split('/')[0]}"
            fn = getattr(self, name, None)
            if fn is None:
                return self._err("not found", 404)
            try:
                return fn(q, path)
            except HttpError as e:
                return self._err(f"network: {e}", 502)
            except (ValueError, KeyError, IndexError) as e:
                return self._err(str(e), 400)

        STATIC_TYPES = {".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

        def _static(self, rel: str):
            """Bundled assets only (fonts, logo); no directory traversal, no HTML."""
            target = (STATIC / rel).resolve()
            ctype = self.STATIC_TYPES.get(target.suffix.lower())
            if not ctype or STATIC.resolve() not in target.parents or not target.is_file():
                return self._err("not found", 404)
            self.send_response(200)
            data = target.read_bytes()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

        # ------------------------------------------------------------- API: state / config
        def GET_state(self, q, path):
            conf = {k: cfg(k) for k in CONFIG_KEYS if k not in HIDDEN_KEYS}
            with app.lock:
                counts = Counter(r["action"] for r in app.rows)
            self._json({
                "version": __version__, "workdir": str(app.workdir), "config": conf,
                "help": CONFIG_KEYS, "auth": app.auth_state(),
                "review_file": app.review_path.name if app.review_path.exists() else "",
                "n_rows": len(app.rows), "counts": counts, "actions": ACTIONS, "types": HAL_TYPES,
                "columns": COLUMNS, "job": {k: v for k, v in app.job.items() if k != "result"},
                "bib_files": [{"name": p.name, "size": p.stat().st_size,
                               "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")}
                              for p in sorted(app.workdir.glob("*.bib"), key=lambda p: -p.stat().st_mtime)],
            })

        def POST_config(self, q, path):
            data = self._jbody()
            values = data.get("values", {})
            for k, v in values.items():
                if k not in CONFIG_KEYS or k in HIDDEN_KEYS:
                    continue
                v = str(v).strip().replace("\n", " ")
                os.environ[k] = v
                dom.write_env_value(k, v, app.env_path)
            self._json({"ok": True})

        # ------------------------------------------------------------- API: HAL sign-in
        def GET_auth(self, q, path):
            self._json(app.auth_state())

        def POST_auth(self, q, path):
            action = path.rstrip("/").split("/")[-1]
            data = self._jbody()
            if action == "login":
                login, pwd = str(data.get("login", "")).strip(), str(data.get("password", ""))
                if not login or not pwd:
                    raise ValueError("enter your HAL login and password")
                res = app.check_fn(login, pwd)
                if not res["ok"]:
                    return self._err(res["message"], 401 if res["status"] in (401, 403) else 502)
                cred.forget()
                cred.set_session(login, pwd)
                remembered = cred.remember(login, pwd) if data.get("remember") else False
                return self._json({"ok": True, "remembered": remembered, "auth": app.auth_state()})
            if action == "logout":
                cred.forget()
                return self._json({"ok": True, "auth": app.auth_state()})
            if action == "forget-env":          # delete a plain-text password left in .env by an older version
                _drop_env_keys(app.env_path, ("HAL_LOGIN", "HAL_PASSWORD"))
                os.environ.pop("HAL_LOGIN", None)
                os.environ.pop("HAL_PASSWORD", None)
                return self._json({"ok": True, "auth": app.auth_state()})
            raise ValueError("unknown auth action")

        def GET_structs(self, q, path):
            self._json({"results": search_structures(q.get("q", ""))})

        def GET_domains(self, q, path):
            all_doms = dom.load_domains(refresh=q.get("refresh") == "1")
            codes = dom.split_codes(q.get("codes", ""))
            if codes:
                return self._json({"labels": {c: dom.label(all_doms, c) for c in codes}})
            res = dom.search(all_doms, q.get("q", ""))
            self._json({"results": res[:200], "total": len(all_doms), "found": len(res)})

        # ------------------------------------------------------------- API: import / review
        def POST_import(self, q, path):
            """Analyse a BibTeX file: uploaded (body) or already in the working folder (?reuse=name.bib).

            mode=merge (default when a review exists): references already reviewed are kept exactly as they are
            (no new queries); only new ones are analysed and added. mode=replace: start a new review (backup kept).
            """
            reuse = q.get("reuse", "")
            if reuse:
                src = app.workdir / Path(reuse).name
                if src.suffix.lower() != ".bib" or not src.is_file():
                    raise ValueError(f"file not found: {reuse}")
                raw, name = src.read_bytes(), src.name
            else:
                raw = self._body()
                if not raw.strip():
                    raise ValueError("empty file")
                name = re.sub(r"[^\w.\-]", "_", Path(q.get("name") or "import.bib").name) or "import.bib"
                if not name.lower().endswith(".bib"):
                    name += ".bib"
                (app.workdir / name).write_bytes(raw)
            text = raw.decode("utf-8", errors="replace")
            limit = int(q.get("limit") or 0)
            no_crossref, no_hal = q.get("no_crossref") == "1", q.get("no_hal") == "1"
            mode = "replace" if q.get("mode") == "replace" or not app.rows else "merge"

            def job():
                def prog(i, n, row):
                    app.progress(i, n, f"{row['action']:<9} {row['hal_type'] or '?':<9} {row['year'] or '----'}  "
                                       f"{row['title'][:90]}")
                with app.lock:
                    known = list(app.rows) if mode == "merge" else None
                if known is not None:
                    app.progress(0, 0, f"Keeping the {len(known)} references you already reviewed; "
                                       "analysing only the new ones…")
                rows = pl.review_bib(text, no_crossref, no_hal, limit, progress=prog, known=known)
                with app.lock:
                    if app.review_path.exists():   # never lose a previous review: keep a dated copy
                        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                        shutil.copy2(app.review_path, app.review_path.with_name(f"review-backup-{stamp}.csv"))
                    if mode == "merge":
                        app.rows, stats = pl.merge(app.rows, rows)
                    else:
                        app.rows, stats = rows, {"kept": 0, "added": len(rows)}
                    app.save_rows()
                app.progress(app.job["n"], app.job["n"], f"Done: {stats['added']} new reference(s) added, "
                                                         f"{stats['kept']} kept as reviewed.")
                return {"mode": mode, **stats, "counts": dict(Counter(r["action"] for r in app.rows))}

            if not app.start_job("review", job):
                return self._err("another task is running", 409)
            self._json({"ok": True, "saved_as": name, "mode": mode})

        def GET_export(self, q, path):
            """The review as a BibTeX file (corrected metadata), to keep or reuse in a reference manager."""
            with app.lock:
                rows = [r for r in app.rows if r["action"] != "duplicate"]
            body = write_bibtex(rows).encode("utf-8")
            (app.workdir / "bib2hal-export.bib").write_bytes(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bibtex; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="bib2hal-export.bib"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def POST_pdf(self, q, path):
            """Attach (upload) or remove the full-text PDF of one reference; stored in the working folder."""
            i = int(path.rstrip("/").split("/")[-1])
            with app.lock:
                r = app.rows[i]
            if q.get("remove") == "1":
                with app.lock:
                    r["pdf"] = ""
                    app.save_rows()
                return self._json({"row": app.row_view(i, r, dom.cached_domains())})
            data = self._body()
            if not data.startswith(b"%PDF"):
                raise ValueError("this file is not a PDF")
            folder = app.workdir / "pdfs"
            folder.mkdir(exist_ok=True)
            target = folder / (re.sub(r"[^\w.\-]", "_", r["key"] or f"ref{i}") + ".pdf")
            target.write_bytes(data)
            with app.lock:
                r["pdf"] = str(target)
                app.save_rows()
            self._json({"row": app.row_view(i, r, dom.cached_domains()), "name": q.get("name", target.name)})

        def GET_structnames(self, q, path):
            """Names for structure ids (from your saved labs first, then from HAL)."""
            ids = [x for x in re.split(r"[;,\s]+", q.get("ids", "")) if x.isdigit()]
            known = _saved_struct_names()
            missing = [x for x in ids if x not in known and x not in app.struct_names]
            if missing:
                try:
                    app.struct_names.update(structure_names(missing))
                except HttpError:
                    pass
            self._json({"names": {x: known.get(x) or app.struct_names.get(x, "") for x in ids}})

        def GET_job(self, q, path):
            self._json(app.job)

        def GET_rows(self, q, path):
            all_doms = dom.cached_domains()
            with app.lock:
                attempts = pl.last_attempts(app.log_path)
                rows = [app.row_view(i, r, all_doms, attempts) for i, r in enumerate(app.rows)]
            codes = set(dom.split_codes(cfg("BIB2HAL_DOMAINS")))
            for r in rows:
                codes.update(dom.split_codes(r.get("domain", "")))
            self._json({"rows": rows, "shortlist": dom.split_codes(cfg("BIB2HAL_DOMAINS")),
                        "labels": {c: dom.label(all_doms, c) for c in codes}})

        def POST_rows(self, q, path):
            i = int(path.rstrip("/").split("/")[-1])
            data = self._jbody()
            with app.lock:
                r = app.rows[i]
                for k, v in data.items():
                    if k in COLUMNS and k not in ("missing",):
                        r[k] = str(v).strip()
                if r["action"] == "deposit":
                    r["missing"] = ", ".join(missing_fields(r))
                app.save_rows()
                view = app.row_view(i, r, dom.cached_domains())
            self._json({"row": view})

        def POST_bulk(self, q, path):
            """Apply the same change to several rows: {'ids': [...], 'fields': {...}, 'add_domain': 'code'}."""
            data = self._jbody()
            ids = [int(i) for i in data.get("ids", [])]
            fields = {k: str(v).strip() for k, v in (data.get("fields") or {}).items()
                      if k in COLUMNS and k not in ("missing", "key")}
            add_dom = str(data.get("add_domain") or "").strip()
            if fields.get("action") and fields["action"] not in ACTIONS:
                raise ValueError(f"unknown action {fields['action']}")
            if fields.get("hal_type") and fields["hal_type"] not in HAL_TYPES:
                raise ValueError(f"unknown type {fields['hal_type']}")
            with app.lock:
                for i in ids:
                    r = app.rows[i]
                    r.update(fields)
                    if add_dom:
                        codes = dom.split_codes(r.get("domain", ""))
                        if add_dom not in codes:
                            r["domain"] = ";".join(codes + [add_dom])
                    if r["action"] == "deposit":
                        r["missing"] = ", ".join(missing_fields(r))
                app.save_rows()
            self._json({"ok": True, "changed": len(ids)})

        def GET_tei(self, q, path):
            i = int(path.rstrip("/").split("/")[-1])
            with app.lock:
                r = dict(app.rows[i])
            pdf = os.path.basename(r["pdf"]) if r.get("pdf") else ""
            self._json({"xml": build_tei(r, cfg("BIB2HAL_IDHAL"), pdf_name=pdf).decode("utf-8")})

        # ------------------------------------------------------------- API: deposit
        def GET_ready(self, q, path):
            with app.lock:
                ready, blocked = pl.split_ready(app.rows)
            done = pl.already_done(app.log_path)
            tested = {e["key"]: e for e in pl.read_log(app.log_path) if e["target"] == "hal-test"}
            attempts = pl.last_attempts(app.log_path)
            self._json({
                "ready": [{"key": r["key"], "title": r["title"], "year": r["year"], "hal_type": r["hal_type"],
                           "done": r["key"] in done,
                           "error": _last_error(attempts.get(r["key"])),
                           "test": ({"ok": tested[r["key"]]["ok"] == "True", "message": tested[r["key"]]["message"]}
                                    if r["key"] in tested else None)}
                          for r in ready],
                "blocked": [{"key": r["key"], "title": r["title"], "problems": p,
                             "error": _last_error(attempts.get(r["key"]))} for r, p in blocked],
                "failed": [{"key": k, "title": e["title"], **_last_error(e)} for k, e in attempts.items()
                           if e.get("ok") != "True" and k not in done],
                "auth": app.auth_state(),
            })

        def POST_deposit(self, q, path):
            data = self._jbody()
            test = bool(data.get("test"))
            if not test and data.get("confirm") != "yes":
                raise ValueError("please confirm the deposit")
            c = cred.get()
            if not c:
                raise ValueError("not signed in to HAL (Settings → HAL account)")
            login, pwd = c
            keys = set(data.get("keys") or [])
            with app.lock:
                ready, _ = pl.split_ready(app.rows)
            done = set() if test else pl.already_done(app.log_path)
            todo = [r for r in ready if (not keys or r["key"] in keys) and r["key"] not in done]
            limit = int(data.get("limit") or 0)
            if limit:
                todo = todo[:limit]

            def job():
                def prog(i, n, e):
                    app.progress(i, n, f"{'CHECK ' if test else ''}{'OK ' if e['ok'] else 'ERR'} {e['status']} "
                                       f"{e['hal_id']:<20} {e['title'][:70]}"
                                       + ("" if e["ok"] else f"  — {e['message'][:200]}"))
                res = pl.deposit(todo, login, pwd, app.log_path, progress=prog, deposit_fn=app.deposit_fn, test=test)
                dups = {e["key"]: e["duplicate_of"] for e in res if e.get("duplicate_of")}
                if dups:                                  # HAL already has them: switch to 'attach'
                    with app.lock:
                        for r in app.rows:
                            if r["key"] in dups:
                                r.update(action="attach", hal_status="in_hal_not_linked", hal_id=dups[r["key"]],
                                         hal_url=f"https://hal.science/{dups[r['key']]}")
                        app.save_rows()
                if not res:
                    app.progress(0, 0, "Nothing new to send (already in HAL).")
                return {"ok": sum(1 for e in res if e["ok"]), "errors": sum(1 for e in res if not e["ok"])}

            if not app.start_job("check" if test else "deposit", job):
                return self._err("another task is running", 409)
            self._json({"ok": True, "count": len(todo)})

        def GET_log(self, q, path):
            self._json({"log": pl.read_log(app.log_path)[::-1]})

    return Handler


def _last_error(e: dict | None) -> dict | None:
    if not e or e.get("ok") == "True":
        return None
    return {"what": "check" if e["target"].endswith("-test") else "submission", "status": e["status"],
            "message": e["message"], **explain(e["message"], e["status"])}


def _saved_struct_names() -> dict[str, str]:
    """BIB2HAL_STRUCT_NAMES='123:LIG — Laboratoire…|456:UGA — …' -> {id: name}."""
    out = {}
    for part in cfg("BIB2HAL_STRUCT_NAMES").split("|"):
        sid, _, name = part.partition(":")
        if sid.strip().isdigit():
            out[sid.strip()] = name.strip()
    return out


def _drop_env_keys(env: Path, keys) -> None:
    if not env.exists():
        return
    lines = env.read_text(encoding="utf-8").splitlines()
    keep = [ln for ln in lines if not any(re.match(rf"\s*{k}\s*=", ln) for k in keys)]
    env.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def serve(port: int = 0, open_browser: bool = True, workdir: Path | None = None, app: App | None = None):
    app = app or App(workdir or Path.cwd())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    return httpd, app, url


def run(port: int = 0, open_browser: bool = True) -> int:
    httpd, app, url = serve(port)
    print(f"bib2hal web interface: {url}\nWorking folder: {app.workdir}\n(Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
