"""HTTP, text-normalisation and configuration helpers (standard library only)."""
from __future__ import annotations

import difflib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

# --------------------------------------------------------------------------- config

CONFIG_KEYS = {
    "BIB2HAL_ME": "Your name as it appears in references, 'Family, Given' (e.g. 'Muller, Sandrine').",
    "BIB2HAL_IDHAL": "Your IdHAL string (e.g. 'sandrine-muller'). Used to tell linked/unlinked HAL records apart.",
    "BIB2HAL_STRUCTS": "Default HAL structure ids for YOUR affiliation, ';'-separated (find them with `bib2hal struct`).",
    "BIB2HAL_STRUCTS_SINCE": "Only pre-fill BIB2HAL_STRUCTS for works published this year or later.",
    "BIB2HAL_DOMAINS": "Your shortlist of HAL disciplines, ';'-separated (set it with `bib2hal domains --pick`).",
    "BIB2HAL_DOMAIN": "Fallback HAL domain when no shortlist is set. Default: info.",
    "BIB2HAL_MAILTO": "Contact e-mail sent to Crossref (optional, gives faster 'polite' access).",
}


def load_env(path: str | os.PathLike = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (existing vars win)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = re.sub(r"\s+#.*$", "", v)          # inline comment: KEY=value   # note
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# --------------------------------------------------------------------------- text

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)          # JATS / HTML tags
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def similarity(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def norm_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower()


def same_person(a: dict, family: str, given: str) -> bool:
    """Loose author match: same family name, compatible first initial."""
    fa, fb = norm_title(a.get("family", "")), norm_title(family)
    if not fa or fa != fb:
        return False
    ga, gb = norm_title(a.get("given", "")), norm_title(given)
    return not ga or not gb or ga[0] == gb[0]


# --------------------------------------------------------------------------- http

class HttpError(RuntimeError):
    pass


class BlockedError(HttpError):
    """The server answered with a bot-protection / rate-limit page instead of data: stop, do not insist."""


# Minimum delay between two requests to the same host, to stay a polite API client.
MIN_INTERVAL = {"api.archives-ouvertes.fr": 1.0, "api.crossref.org": 0.2}
_last_call: dict[str, float] = {}
_BLOCK_MARKERS = ("anubis", "making sure you're not a bot", "proof-of-work", "captcha", "cf-challenge",
                  "access denied", "too many requests")


def _throttle(url: str) -> None:
    host = urllib.parse.urlsplit(url).hostname or ""
    wait = MIN_INTERVAL.get(host, 0.0) - (time.monotonic() - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _blocked_message(url: str, status: int) -> str:
    host = urllib.parse.urlsplit(url).hostname or url
    return (f"{host} answered with a protection page (HTTP {status}) instead of data. bib2hal stopped to avoid "
            f"insisting. Wait a while before trying again; if it persists, contact the service's support.")


def looks_blocked(status: int, body: str, content_type: str = "") -> bool:
    """True when a JSON/XML API answered with an HTML challenge page or a rate limit."""
    if status == 429:
        return True
    head = (body or "")[:3000].lower()
    is_html = "text/html" in content_type.lower() or head.lstrip().startswith(("<!doctype html", "<html"))
    return is_html and any(m in head for m in _BLOCK_MARKERS)


def _ua() -> str:
    mail = cfg("BIB2HAL_MAILTO")
    return f"bib2hal/{__version__} (https://github.com/AI4Health-DAISY-LIG/bib2hal" + (f"; mailto:{mail})" if mail else ")")


def get_json(url: str, params: dict | None = None, retries: int = 3, timeout: int = 30):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        _throttle(url)
        req = urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                if looks_blocked(r.status, raw, r.headers.get("Content-Type", "")):
                    raise BlockedError(_blocked_message(url, r.status))
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as e:
                    raise HttpError(f"GET {url}: answer is not JSON") from e
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            if looks_blocked(e.code, body, e.headers.get("Content-Type", "") if e.headers else ""):
                raise BlockedError(_blocked_message(url, e.code)) from e
            last = e
            if e.code not in (500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2.0 * (attempt + 1))
    raise HttpError(f"GET {url} failed: {last}")


def request(url: str, data: bytes | None = None, headers: dict | None = None, method: str = "GET",
            timeout: int = 120) -> tuple[int, str]:
    """Raw HTTP call. Returns (status, body); status 0 means the server could not be reached.

    Raises BlockedError when the server answers with a bot-protection page or a rate limit.
    """
    _throttle(url)
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": _ua(), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            if looks_blocked(r.status, body, r.headers.get("Content-Type", "")):
                raise BlockedError(_blocked_message(url, r.status))
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        if looks_blocked(e.code, body, e.headers.get("Content-Type", "") if e.headers else ""):
            raise BlockedError(_blocked_message(url, e.code)) from e
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def post_bytes(url: str, data: bytes, headers: dict, timeout: int = 120) -> tuple[int, str]:
    return request(url, data, headers, method="POST", timeout=timeout)
