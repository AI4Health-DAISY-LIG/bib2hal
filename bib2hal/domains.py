"""HAL disciplines ("domains"): fetch the full list, search it, pick a personal shortlist,
and suggest a domain for each paper from that shortlist."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .util import get_json, norm_title

REF_DOMAIN = "https://api.archives-ouvertes.fr/ref/domain/"
CACHE = Path.home() / ".bib2hal" / "hal_domains.csv"
FIELDS = ["code", "label_en", "label_fr", "level"]


def fetch_domains() -> list[dict]:
    """Download every HAL domain from the HAL referential API."""
    d = get_json(REF_DOMAIN, {"q": "*:*", "fl": "*", "rows": 2000, "wt": "json"}) or {}
    out = []
    for doc in d.get("response", {}).get("docs", []):
        code = doc.get("code_s") or doc.get("code") or ""
        if not code:
            continue
        out.append({
            "code": code,
            "label_en": doc.get("en_domain_s") or doc.get("label_s") or doc.get("en_label_s") or "",
            "label_fr": doc.get("fr_domain_s") or doc.get("fr_label_s") or "",
            "level": str(doc.get("level_i", code.count("."))),
        })
    out.sort(key=lambda x: x["code"])
    return out


def load_domains(refresh: bool = False, cache: Path = CACHE) -> list[dict]:
    """Cached domain list (~/.bib2hal/hal_domains.csv); downloaded on first use or with refresh."""
    if cache.exists() and not refresh:
        with open(cache, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    doms = fetch_domains()
    if doms:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(doms)
    return doms


def cached_domains(cache: Path = CACHE) -> list[dict]:
    """Domain list if already cached, else [] (never hits the network)."""
    return load_domains(cache=cache) if cache.exists() else []


def search(domains: list[dict], terms: str) -> list[dict]:
    """All domains whose code or labels contain every term (accent/case-insensitive)."""
    words = norm_title(terms).split()
    if not words:
        return domains
    res = []
    for d in domains:
        hay = norm_title(" ".join([d["code"].replace(".", " "), d["code"], d["label_en"], d["label_fr"]]))
        if all(w in hay for w in words):
            res.append(d)
    return res


def label(domains: list[dict], code: str) -> str:
    for d in domains:
        if d["code"] == code:
            return d["label_en"] or d["label_fr"]
    return "?"


def unknown_codes(codes: str, domains: list[dict]) -> list[str]:
    known = {d["code"] for d in domains}
    return [c for c in split_codes(codes) if known and c not in known]


def split_codes(codes: str) -> list[str]:
    return [c for c in re.split(r"[;,\s]+", codes or "") if c]


def _stems(text: str) -> set[str]:
    stop = {"and", "the", "for", "with", "des", "les", "and", "from", "sur", "dans", "une", "other", "autre"}
    return {w[:5] for w in norm_title(text).split() if len(w) > 3 and w not in stop}


def suggest(shortlist: list[str], domains: list[dict], text: str) -> str:
    """Pick the shortlist domain whose label best overlaps the paper's text; first one if no overlap."""
    if not shortlist:
        return ""
    if not domains:
        return shortlist[0]
    words = _stems(text)
    best, best_s = shortlist[0], 0
    for code in shortlist:
        d = next((x for x in domains if x["code"] == code), None)
        if not d:
            continue
        # the most specific part of the label counts double
        leaf = re.split(r"[/>]", d["label_en"] or d["label_fr"])[-1]
        s = len(words & _stems(d["label_en"] + " " + d["label_fr"])) + len(words & _stems(leaf))
        if s > best_s:
            best, best_s = code, s
    return best


def write_env_value(key: str, value: str, env: Path = Path(".env")) -> None:
    """Set KEY=value in .env (replace the line if present, else append)."""
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, done = [], False
    for line in lines:
        if re.match(rf"\s*{re.escape(key)}\s*=", line):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
