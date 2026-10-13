# bib2hal

Bring your publication list into [HAL](https://hal.science) from a BibTeX file (Google Scholar, Zotero, ORCID, publishers…),
**with a review step before anything is submitted**.

📖 **User guide and downloads: https://ai4health-daisy-lig.github.io/bib2hal/**

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

## Download (no installation)

Get the file for your system from the **[latest release](https://github.com/AI4Health-DAISY-LIG/bib2hal/releases/latest)**:

| System | File |
|---|---|
| Windows 10/11 | `bib2hal-windows-x64.exe` |
| macOS, Apple Silicon (M1 and later) | `bib2hal-macos-arm64.zip` |
| Linux | `bib2hal-linux-x64.zip` |

Double-click it: a small window opens (keep it open while you work — closing it stops bib2hal) and the interface
appears in your browser. Your settings and review files are kept in **Documents/bib2hal**.

The application is not signed by Microsoft or Apple yet, so the first launch shows a warning:
- **Windows** — "Windows protected your PC": click *More info* → *Run anyway*.
- **macOS** — "cannot be opened because the developer cannot be verified": unzip, then *right-click* the file →
  *Open* → *Open* (only the first time).
- **Linux** — unzip, then run `./bib2hal-linux-x64`.

You can check a download against `SHA256SUMS.txt`, published with each release.

## Install with Python (alternative)

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
BIB2HAL_STRUCTS=12345;67890           # your labs (HAL structure ids), offered for each paper — never filled in automatically
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

## Releasing a new version (maintainers)

1. Update `__version__` in `bib2hal/__init__.py` (and `version` in `pyproject.toml`), commit and push.
2. Tag and push the tag: `git tag v0.2.0 && git push origin v0.2.0`.
3. GitHub Actions (`.github/workflows/release.yml`) runs the tests, builds the three executables with PyInstaller
   (`packaging/bib2hal.spec`) on Windows, macOS and Linux, and publishes them in a GitHub Release with checksums.

4. The same run then re-publishes the documentation website (`docs/`, workflow `.github/workflows/pages.yml`) on
   GitHub Pages. It is also re-published whenever `docs/` changes on `main`.

`.github/workflows/ci.yml` runs the tests on the three systems at every push and pull request.

One-time GitHub settings for the website: **Settings → Pages → Source: GitHub Actions**, and
**Settings → Environments → github-pages → Deployment branches and tags**: add the tag rule `v*`
(otherwise the deployment triggered by a release tag is refused).

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
