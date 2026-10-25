# Pleochroic Search

Pleochroic Search is Pleochroic's private fork of [SearXNG](https://github.com/searxng/searxng),
a metasearch engine. It runs as an internal service on Pleochroic's service box, behind
WireGuard, and is called exclusively by `standalone-service` (the Node account/tools service
in this same repository's parent, `standalone-service/`) over `127.0.0.1` — it is never
exposed publicly. `standalone-service` talks to it through SearXNG's own JSON API
(`GET /search?q=…&format=json`).

This directory (`search/`) is **AGPL-3.0-or-later**, same as upstream SearXNG — see `../LICENSE`
at the root of this checkout, which is the original, unmodified SearXNG licence file. Everything
else in `standalone-service/` (the Node service, the portal, etc.) is MIT; the two licences live
side by side in the same repository, each covering its own subtree. See the root
`standalone-service/README.md` for how the two pieces fit together.

## What was changed vs. upstream

The guiding rule for this fork is: **change as little as possible**, so that pulling in upstream
SearXNG releases later is a small, mostly-conflict-free rebase. Concretely:

- **Branding** (commits `templates: Show instance_name instead of hardcoded SearXNG brand`,
  `settings: Rename default instance to Pleochroic Search`, `tests: Update branding assertions
  for Pleochroic Search`): the handful of places where SearXNG's UI hardcodes the literal string
  `"SearXNG"` instead of using the existing `{{ instance_name }}` template variable were changed
  to use that variable (homepage `<h1>`, the hidden a11y label next to the search-page logo mark,
  `<meta description/keywords>`, the OpenSearch descriptor, the RSS feed title/description). The
  default `instance_name` itself was changed from `"SearXNG"` to `"Pleochroic Search"` in
  `searx/settings.yml`. The corresponding assertions in `tests/unit/test_webapp.py` were updated
  to match.
- **What was deliberately left alone, per AGPL §13 and plain attribution courtesy**: the footer's
  *"Powered by [SearXNG](…/about) - `<version>` — a privacy-respecting, open metasearch engine"*,
  its "Source code" / "Issue tracker" links, and the `/info/en/about` page are all untouched —
  they still say SearXNG and link to the upstream project, as they should. The brand changes,
  the attribution doesn't.
- **Known, intentionally-skipped branding spots** (documented here instead of "fixed" in the
  diff, to keep the diff small and because they need real design work, not text edits):
  - `searx/static/themes/simple/img/searxng.png` — a 640×110 PNG that spells out "SearXNG" as a
    wordmark. It's shown on the preferences/about/stats pages (`page_with_header.html`). Rather
    than invent a logo, that `<img>` was replaced with a plain-text `<span>{{ instance_name }}</span>`
    (styled inline, no new CSS file). A real Pleochroic wordmark can replace this later.
  - A handful of *descriptive* sentences on the preferences pages still say "SearXNG" (e.g. "This
    is the list of cookies … SearXNG is storing on your computer"). These are `gettext()`
    translation strings shared across every locale's `.po` file; rewriting the English source
    string would silently break every other language's translation and touch files well outside
    this fork's actual scope. Left as upstream wrote them — low-visibility, internal-tool-only
    UI, not worth the translation churn.
  - `searx/favicon.svg`/`.png` and the small magnifying-glass mark used as the search-page
    "wordmark" (`searxng-wordmark.min.svg`) are abstract icon marks, not literal "SearXNG" text —
    left as-is.

## Engine selection

`pleochroic/settings.yml` uses `use_default_settings.engines.keep_only` — a deny-by-default
allowlist — rather than disabling categories one by one. Only these engines survive the filter:

| Group | Engines |
|---|---|
| General web | google, bing, duckduckgo, brave, mojeek, startpage |
| Code / Q&A | github, stackoverflow, askubuntu, superuser |
| Reference | wikipedia, mdn, mankier, arch linux wiki |
| Research | arxiv |
| Package registries | pypi, npm, crates.io, docker hub |

This automatically removes SearXNG's entire images/videos/music/social-media/torrent surface
(and everything else in its ~350 built-in engines) — there's no `images`/`videos`/`music`/`social
media` engine left to enable, rather than relying on remembering to keep all of those switched
off individually. `search.safe_search` is also set to `1` as a light extra safety net.

A few of the kept engines needed per-engine overrides because upstream ships them off by default:
`google` and `bing` (`disabled: true`), `npm` and `crates.io` (`disabled: true`), and `mojeek` /
`startpage` (`inactive: true` — both use a proof-of-work CAPTCHA upstream is cautious about; they're
re-enabled here but given `weight: 0.5` since they're more likely to come back empty than the rest).

**Known gap**: there is no Maven Central search engine in upstream SearXNG as of this fork (only
`pypi`, `npm`, `crates.io`, `hex`, `rubygems`, `packagist` exist as registry engines). Adding one
would mean writing a new `searx/engines/*.py` module — real code, not a settings change — so it's
out of scope here and left as a follow-up if it's ever needed.

## Settings layering

SearXNG itself only ever loads **one** user settings file — whatever `SEARXNG_SETTINGS_PATH`
points at (a file or a folder containing `settings.yml`), merged once onto its own
`searx/settings.yml` defaults via `use_default_settings` (see `searx/settings_loader.py`). There
is no built-in mechanism for a *second* overlay file, which is a problem here: the public,
committed settings template (`pleochroic/settings.yml`) must never contain the residential
proxy's real credentials, but the running service needs them.

The fix is a tiny deploy-time generator, `pleochroic/merge_settings.py`: it deep-merges the
public template with a private, root-owned overlay file and writes the result to the path
SearXNG actually reads. Nothing upstream had to change for this — it's just a ~40-line script
using PyYAML, which SearXNG already depends on.

```
pleochroic/settings.yml                  (public, committed, no secrets)
        +
/etc/pleochroic-search/settings.private.yml   (root-owned, 0640, real proxy URL — NOT in git)
        =  merge_settings.py  =>
/run/pleochroic-search/settings.yml      (generated fresh on every service start)
```

`pleochroic-search.service`'s `ExecStartPre` runs the merge into a systemd `RuntimeDirectory=`
(`/run/pleochroic-search`, tmpfs, owned by the service user, gone on stop) before `ExecStart`
launches SearXNG with `SEARXNG_SETTINGS_PATH` pointing at that generated file. This means:

- Editing `/etc/pleochroic-search/settings.private.yml` and running
  `systemctl restart pleochroic-search` is enough to rotate the proxy credentials — no reinstall.
- `/etc/pleochroic-search/settings.yml` (the deployed copy of the public template) only ever gets
  overwritten by `install.sh`; `settings.private.yml` is only ever *scaffolded* by `install.sh`
  if missing, and is never touched again — real credentials survive re-running the installer.
- If `settings.private.yml` doesn't exist yet, `merge_settings.py` falls through to writing the
  public settings unchanged (no proxy configured) rather than failing — useful on first boot, and
  exactly what was used for local verification below (no proxy on this machine).

`server.secret_key` is handled differently and doesn't go through this merge at all: SearXNG's
own `searx/settings_defaults.py` reads the `SEARXNG_SECRET` environment variable directly
(`environ_name='SEARXNG_SECRET'`), unconditionally overriding whatever is in any settings.yml.
It travels via `EnvironmentFile=/etc/pleochroic-search/secrets.env` (root-owned, `0600`) in the
systemd unit, which systemd itself reads as root before dropping to the service user — so unlike
`settings.private.yml`, it never needs to be group-readable.

## Deployment

Target: Ubuntu 24.04, Python ≥ 3.10 (`setup.py`'s own requirement; Ubuntu 24.04 ships 3.12).

```sh
git clone <this fork's URL>    # or: already checked out at standalone-service/search
cd standalone-service/search
sudo pleochroic/install.sh
```

`install.sh` is idempotent (see its own header comment for the full step list): OS packages →
system user `pleochroic-search` → code synced to `/opt/pleochroic-search/release` → venv at
`/opt/pleochroic-search/venv` with `requirements.txt` + `requirements-server.txt` (this is where
[Granian](https://github.com/emmett-framework/granian) comes from — SearXNG's own
`requirements-server.txt` pins it; see "Why Granian" below) → `/etc/pleochroic-search/` (`0750`,
`root:pleochroic-search`) laid out → systemd unit installed + `daemon-reload`.

It deliberately does **not** start the service. After the first run:

1. Edit `/etc/pleochroic-search/settings.private.yml` with the real proxy URL (see
   `settings.private.example.yml` for the exact key and format).
2. `systemctl enable --now pleochroic-search`
3. `curl -s '127.0.0.1:8888/search?q=test&format=json'`
4. `journalctl -u pleochroic-search -f`

### Why Granian, not uWSGI

SearXNG's docs (`docs/admin/installation-uwsgi.rst` vs. `docs/admin/installation-granian.rst`)
currently describe Granian as "the future replacement for uWSGI […] at the moment, it's only
officially supported in the container installation." In practice, though, SearXNG's own official
container (`container/Dockerfile`, `container/entrypoint.sh`) already runs
`granian searx.webapp:app` exclusively, and ships a dedicated `requirements-server.txt` pinning
`granian` for exactly this purpose — uWSGI's documented path assumes Debian's distro-packaged
`uwsgi` + Emperor/vassal layout (`utils/searxng.sh`), which doesn't fit a single self-contained
`pleochroic-search.service` unit nearly as cleanly, and Granian ships prebuilt wheels (no C
toolchain needed to install it, unlike building `uwsgi` from source). Granian was chosen here,
configured the same way the official container does (`GRANIAN_INTERFACE=wsgi`, `searx.webapp:app`).

### No Valkey

The rate limiter and its botdetection middleware are off (`server.limiter: false`,
`server.public_instance: false` — the latter is upstream's own default already; the fork makes
both explicit in `pleochroic/settings.yml`). Verified by reading `searx/limiter.py`:
`initialize()` returns immediately when both flags are false, and never touches Valkey — a
missing/absent Valkey connection is a no-op in that path (`searx/valkeydb.py`'s `client()`
returns `None` when `valkey.url` is unset, and `searx/botdetection/__init__.py`'s `init()`
handles a `None` client gracefully). This was confirmed in the local run below: SearXNG starts
and serves `/search?format=json` correctly without any Valkey instance running, with only a
benign `WARNING:searx.botdetection.config: missing config file: …/limiter.toml` in the log (the
botdetection module still initializes its config object even though nothing calls it) — expected
and harmless, not a crash.

### Systemd hardening

`pleochroic-search.service` is locked down about as far as a bare-metal (non-container) Python
service reasonably can be: `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateDevices`,
`ProtectHome`, no capabilities, restricted syscalls (`@system-service`), `RestrictNamespaces`,
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, etc. — see the unit file for the full list
and inline comments. One knob, `MemoryDenyWriteExecute`, is deliberately left **off** with a
comment explaining why: `curl_cffi` (used for all outgoing engine requests) loads `cffi`-based
native extensions, and whether their runtime behaviour is compatible with that restriction
wasn't verified against the real Linux/Granian/curl_cffi combination — this fork was only
verified on macOS, which can't exercise Linux-specific systemd sandboxing at all. Worth trying on
the real server and watching the journal for `SIGSYS` kills before keeping it enabled.

## AGPL obligation

This is a *modified* AGPL-3.0-or-later work, offered as a network service (even though the
network it's offered on is just WireGuard + `127.0.0.1` inside Pleochroic's own infrastructure —
AGPL §13 doesn't carve out an exception for "internal" services reachable by any user over a
network). That means Pleochroic is obligated to offer anyone interacting with the running
service a way to get this fork's actual corresponding source — not just a link to unmodified
upstream SearXNG.

**This has not been done yet** — the footer's "Source code" link (`searxng_git_url`, driven by
`searx/version.py`'s `GIT_URL`, which is read from this checkout's own `git remote`) still points
at `https://github.com/searxng/searxng` because that's still this clone's `origin`. Pleochroic
will publish this fork (this `pleochroic` branch, or a mirror of it) and either re-point `origin`
at that URL, or set `brand.*` / rebuild so `GIT_URL` reflects it, before the service is "offered"
to anyone beyond the one Node process that calls it today. Track this before loosening the
service's current `127.0.0.1`-only exposure in any way.

## Rebasing on upstream

```sh
git fetch origin                 # origin is still the real searxng/searxng, deliberately
git checkout pleochroic
git rebase origin/master
```

The diff from upstream is intentionally small (a handful of template one-liners, one default
settings value, four test assertions, plus everything new under `pleochroic/`), so conflicts
should be rare and, when they happen, easy to resolve — most of this fork's own files
(`pleochroic/*`) don't exist upstream at all and can't conflict. The engine `keep_only` list in
`pleochroic/settings.yml` matches engine *names*, not Python code, so a rebase won't silently
re-enable anything; it will just mean a newly-added upstream engine isn't included until someone
adds it to the list on purpose (deny-by-default, see "Engine selection" above).

## Local verification

Verified on macOS (this fork's development machine), with Python 3.11 in a scratch venv, no
proxy configured (so this is **not** a stand-in for a full deployment check — see the proxy note
below):

```sh
python3.11 -m venv /tmp/pleochroic-search-venv
/tmp/pleochroic-search-venv/bin/pip install -r requirements.txt -r requirements-server.txt
python3 pleochroic/merge_settings.py pleochroic/settings.yml \
    /path/to/nonexistent-settings.private.yml /tmp/merged-settings.yml
SEARXNG_SETTINGS_PATH=/tmp/merged-settings.yml SEARXNG_SECRET=<random> \
GRANIAN_INTERFACE=wsgi GRANIAN_HOST=127.0.0.1 GRANIAN_PORT=28891 \
PYTHONPATH="$(pwd)" /tmp/pleochroic-search-venv/bin/granian searx.webapp:app
```

- `merge_settings.py` correctly handled both the "private file exists" case (proxy URL merged
  into `outgoing.proxies`) and the "private file missing" case (public settings written
  unchanged, with a stderr note) — the latter being what first boot on a freshly-installed server
  looks like before `settings.private.yml` is edited.
- The app started cleanly and `GET /search?q=test&format=json` returned `200` with real,
  well-formed JSON results (`query`, `results`, `answers`, `corrections`, `infoboxes`,
  `suggestions`, `unresponsive_engines` keys) — some engines (`duckduckgo`, `google`, `mojeek`)
  actually came back in `unresponsive_engines` with CAPTCHA/access-denied errors, which is the
  expected shape of going out from this Mac's residential-ish IP with no proxy at all; others
  (`bing`, `brave`, `startpage`, `wikipedia`) returned real results. On the real server, *all*
  outgoing engine traffic must go through the configured residential proxy — this local run
  intentionally skipped that (no credentials exist to test with, and none should ever be
  committed here).
- Branding rendered correctly end to end: `<title>Pleochroic Search</title>`, homepage
  `<h1>Pleochroic Search</h1>`, `opensearch.xml`'s `<ShortName>`/`<LongName>`, and the footer
  still reading `Powered by SearXNG - <version> — …` with its original
  `https://github.com/searxng/searxng` source/issue links.
- `/stats`, `/info/en/about`, `/preferences`, `/opensearch.xml` all returned `200`.
- `yamllint --strict` passed on `searx/settings.yml`, `pleochroic/settings.yml`, and
  `pleochroic/settings.private.example.yml` (SearXNG's own `.yamllint.yml` config).
- SearXNG's unit test suite (`tests/unit`, run via `nose2` — the same tool `make test.unit` uses
  under the hood, invoked directly here rather than through `./manage`'s separate pyenv wrapper):
  **359/359 passed** after updating the four assertions in `tests/unit/test_webapp.py` that
  literally expected the string `"SearXNG"` in rendered output (see the branding section above).
  One pre-existing, unrelated gap was noticed and is **not** caused by this fork: `tests/unit/
  test_engine_tineye.py` imports `requests` directly, which isn't in SearXNG's own
  `requirements.txt`/`requirements-dev.txt` — installing it separately made that test importable
  and it passed too, but this is an upstream gap, not something this fork touched or needs to fix.
