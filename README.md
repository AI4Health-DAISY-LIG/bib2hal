# bib2hal

Bring your publication list into [HAL](https://hal.science) from a BibTeX file (Google Scholar, Zotero, ORCID, publishers…),
**with a review step before anything is submitted**.

```
scholar.bib ──► Import ──► Review ──► Check with HAL ──► Submit to HAL ──► HAL moderation ──► public record
                Crossref    you decide   (creates nothing)   (your account)
                + HAL check  paper by paper
```

- **Runs on your computer.** Your files stay in the folder you work in; the only network traffic is to the public
  Crossref and HAL APIs. No server, no tracking, no dependencies beyond Python ≥ 3.9.
- **Your HAL password is never written to a file.** You sign in once; bib2hal checks the login with HAL and, if you
  wish, remembers it in your operating system's keychain (Windows Credential Manager, macOS Keychain, Linux Secret
  Service).
- **Nothing is submitted by surprise.** *Check with HAL* has HAL validate every record without creating anything;
  *Submit* asks for confirmation, and a reference already submitted is never sent twice.

> Status: early version. The record format sent to HAL has been written from HAL's documentation; use
> **Check with HAL** on a couple of references first and report any error message in the issues.

## Install

```bash
git clone https://github.com/AI4Health-DAISY-LIG/bib2hal.git
cd bib2hal
pip install -e .            # or: uv sync      → gives the `bib2hal` command
pip install keyring         # optional: most robust "remember me" on every system
```

## Use it — web interface

```bash
bib2hal ui                  # or: uv run bib2hal ui
```

Your browser opens on four steps:

1. **Settings** — your name as it appears in references, your IdHAL, your lab (search HAL's structure list), your
   disciplines, and **Sign in to HAL**.
2. **Import** — choose your `.bib` export. Each reference is completed from Crossref and looked up in HAL.
3. **Review** — paper by paper: what to do (submit / already in HAL / skip…), document type, disciplines, missing
   fields in red, things to double-check in amber, TEI preview. Select several papers to change them at once.
4. **Submit** — *Check with HAL* (creates nothing), then *Submit to HAL*. HAL moderators review each record before it
   becomes public.

The page is served by your own computer on `127.0.0.1` only and protected by a random per-session code.
Stop it with Ctrl+C.

## Use it — command line

```bash
bib2hal login                              # once: checked with HAL, remembered in the OS keychain
bib2hal review scholar.bib -o review.csv   # import (nothing is submitted)
bib2hal validate review.csv                # go through each paper in the terminal (or edit the CSV)
bib2hal deposit review.csv --test          # HAL validates everything, creates nothing
bib2hal deposit review.csv                 # submit, after confirmation
bib2hal whoami | bib2hal logout
```

Other helpers: `bib2hal struct "Laboratoire d'informatique de Grenoble"` (structure ids),
`bib2hal domains --pick` (your disciplines), `bib2hal tei review.csv` (write the records to files to inspect them).

## Settings (`.env`)

The web interface writes them for you. By hand, create a `.env` in your working folder (see `.env.example`):

```ini
BIB2HAL_ME=Muller, Sandrine           # your name as in references: Family, Given
BIB2HAL_IDHAL=sandrine-muller         # your IdHAL (to spot records not yet linked to you)
BIB2HAL_STRUCTS=12345                 # HAL structure id(s) of your current lab, ';'-separated
BIB2HAL_STRUCTS_SINCE=2022            # ...only pre-filled for works from this year on
BIB2HAL_DOMAINS=info.info-ai;sdv.neu  # your HAL disciplines
BIB2HAL_MAILTO=you@univ.fr            # optional, polite Crossref access
```

`.env` never contains your HAL password. Never commit `.env` (it is in `.gitignore`).

## Keeping your HAL profile up to date

Submit new papers **in HAL first** (by DOI, at acceptance): HAL pushes them to ORCID, and Google Scholar indexes HAL.
Re-import a fresh Scholar export once or twice a year to catch what slipped through.

## About signing in

HAL's deposit API only accepts a HAL login and password (there is no "Sign in with HAL" for third-party tools).
bib2hal sends them only to HAL over HTTPS, checks them when you sign in, and keeps them either in memory (until it
stops) or in your OS keychain if you tick *Remember me*. *Sign out* removes them from both.

## Privacy (GDPR)

bib2hal is a local tool. It stores nothing outside your working folder and your OS keychain, and sends no telemetry.
Network traffic: Crossref API (titles, first author, DOIs), HAL search and referential APIs (titles, DOIs),
HAL deposit API (your submissions, with your own account). Each service's own privacy policy applies.

## Development

```bash
python -m unittest discover -s tests -v     # offline tests, no network
```

To try deposits against HAL's test server instead of hal.science, set
`BIB2HAL_SWORD_URL=https://api-preprod.archives-ouvertes.fr/sword/hal/` in your environment (developers only).

## En bref (français)

Outil local pour importer dans HAL une liste de publications au format BibTeX (export Google Scholar, Zotero…) :
enrichissement Crossref, détection de ce qui est déjà dans HAL, relecture article par article, **vérification par HAL
sans rien créer**, puis dépôt sous votre compte HAL après confirmation. Votre mot de passe HAL n'est jamais écrit dans
un fichier (trousseau du système ou mémoire seulement).

## License

MIT — © Sandrine Muller, Université Grenoble Alpes / LIG.
