"""Turn HAL's technical error messages into explanations a researcher can act on."""
from __future__ import annotations

import re

# (pattern on the raw message, short title, what to do). First match wins.
_RULES = [
    (r"duplicate-entry.*?\"([a-z]+-\d+)\"",
     "Already in HAL",
     "HAL already has this publication ({0}, same DOI or title). Nothing was created. Set the action to "
     "'attach' and link that record to your IdHAL in HAL (Mon espace → Mes publications)."),
    (r"Already in HAL as ([a-z]+-\d+)",
     "Already in HAL",
     "HAL already has this publication ({0}). Nothing was created. Link that record to your IdHAL in HAL "
     "(Mon espace → Mes publications)."),
    (r"surname'?: This element is not expected\. Expected is \( ?\{[^}]+\}forename",
     "An author has no first name",
     "HAL requires a first name for every author. This is usually a consortium or group listed as an author "
     "(HAL lists people only): remove it from the authors, or add the missing first name."),
    (r"imprint'?: This element is not expected\. Expected is \( ?\{[^}]+\}authority",
     "Thesis details missing",
     "For a thesis, HAL needs the institution and the supervisor(s). Fill in 'Institution' and 'Supervisor(s)'."),
    (r"meeting.*not expected|settlement.*not expected|country.*(not expected|not a valid)",
     "Conference details incomplete",
     "Check the conference name, city, country code (2 letters, e.g. FR) and start date."),
    (r"date.*(not a valid|does not match|is not accepted)",
     "Invalid date",
     "A date is not in a format HAL accepts. Use YYYY, YYYY-MM or YYYY-MM-DD."),
    (r"classCode|halDomain",
     "Discipline not accepted",
     "One of the disciplines is not accepted by HAL. Choose it again from the list."),
    (r"affiliation|struct-",
     "Affiliation not accepted",
     "One of the structure ids is unknown or closed in HAL. Choose your lab again from the list (prefer VALID ones)."),
    (r"This element is not expected",
     "Record format refused",
     "HAL could not read one part of this record. Check the fields required for this document type; "
     "if the error persists, report it with the message below."),
]

_STATUS = {
    0: ("HAL could not be reached", "Check your internet connection and try again later."),
    401: ("Login refused", "HAL refused your login: sign out and sign in again in Settings."),
    403: ("Not allowed", "HAL refused the request for this account. Check that you can deposit in HAL with it."),
    413: ("File too large", "The attached PDF is too large for HAL. Remove it or use a smaller version."),
    415: ("Format not accepted", "HAL did not accept the file format. Attach a PDF."),
}


def explain(message: str, status: int | str = "") -> dict:
    """{'title', 'help'} for a HAL error message (falls back to a generic explanation)."""
    msg = message or ""
    for pat, title, help_ in _RULES:
        m = re.search(pat, msg, re.I | re.S)
        if m:
            return {"title": title, "help": help_.format(*m.groups())}
    try:
        st = int(status)
    except (TypeError, ValueError):
        st = -1
    if st in _STATUS:
        return {"title": _STATUS[st][0], "help": _STATUS[st][1]}
    if "protection page" in msg:
        return {"title": "Blocked by HAL's protection", "help": "HAL's servers are protecting themselves against "
                "robots. bib2hal stopped. Wait a few hours before trying again."}
    return {"title": "HAL refused this record", "help": "See HAL's message below. Fix the field it mentions and "
            "check the record again."}
