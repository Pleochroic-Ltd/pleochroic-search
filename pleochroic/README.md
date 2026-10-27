# Pleochroic Search

Pleochroic Search is Pleochroic's fork of [SearXNG](https://github.com/searxng/searxng), a metasearch engine. It
runs as an internal service on Pleochroic's service host, behind WireGuard, listening on `127.0.0.1` only.
`standalone-service`, the Node account and tools service in the parent repository, is its only caller and uses
SearXNG's JSON API (`GET /search?q=…&format=json`).

This directory (`search/`) is **AGPL-3.0-or-later**, as upstream SearXNG is; `../LICENSE` is SearXNG's original,
unmodified license file. The rest of `standalone-service/` is MIT, each license covering its own subtree. The parent
`README.md` describes how the two fit together.

## Changes from upstream

The fork changes as little as possible, so that rebasing onto upstream releases stays small.

- **Branding**: the places where SearXNG's UI hardcodes `"SearXNG"` now use the existing `{{ instance_name }}`
  template variable: the homepage `<h1>`, the hidden accessibility label next to the search-page logo, `<meta
  description/keywords>`, the OpenSearch descriptor, and the RSS feed title and description. The default
  `instance_name` in `searx/settings.yml` is `"Pleochroic Search"`, and the four assertions in
  `tests/unit/test_webapp.py` that expected `"SearXNG"` follow it.
- **Attribution kept** (AGPL §13 and courtesy): the footer's "Powered by SearXNG - `<version>`", its "Source code"
  and "Issue tracker" links, and the `/info/en/about` page are unchanged and still point at the upstream project.
- **Branding left for later**:
  - `searx/static/themes/simple/img/searxng.png`, a "SearXNG" wordmark shown on the preferences, about and stats
    pages (`page_with_header.html`), is replaced there by a plain-text `<span>{{ instance_name }}</span>` styled
    inline. A Pleochroic wordmark can replace it.
  - Some descriptive sentences on the preferences pages still say "SearXNG". They are `gettext()` strings shared by
    every locale's `.po` file, so they stay as upstream wrote them.
  - `searx/favicon.svg`/`.png` and the search-page mark (`searxng-wordmark.min.svg`) are abstract icons and stay as
    they are.
- **Proxy exit rotation**: `searx/pleochroic_proxy.py`, called once from `Search.search()` in
  `searx/search/__init__.py`. See "Proxy exit rotation" below.

## Proxy exit rotation

The residential proxy's username carries a session id (`…-sid-<id>-…`) that pins one random residential exit, and
the provider moves each session to a new exit every hour on its own. An exit can turn slow or get flagged before
that, and then nearly every engine of a search fails at once.

After each search, `pleochroic_proxy.after_search` counts the engines that sent a request and failed the way a bad
exit fails: timeouts, `curl_cffi` connection, DNS, proxy and TLS errors, CAPTCHAs, 403s and 429s. A site's own HTTP
error does not count. When at least two engines, and at least half of those asked, failed that way, it gives every
proxy URL with a session id its own fresh random one of the same length, drops the old session's clients from the
cache (closing them five minutes later, once any request on them is surely done), and lifts the suspensions engines
hold against the old address, so the next search leaves from another exit. It swaps at most once a minute per
process (granian runs two), so concurrent failing searches do not churn through exits. A proxy URL without a `-sid-`
part is left alone.

The new id lives only in memory: a restart goes back to the one in `settings.private.yml`, which the provider has
moved to a new exit within the hour anyway. The id is part of the proxy credential and is never logged.

## Engine selection

`pleochroic/settings.yml` uses `use_default_settings.engines.keep_only`, a deny-by-default allowlist. Only these
engines remain:

| Group | Engines |
|---|---|
| General web | google, bing, duckduckgo, brave, mojeek, startpage |
| Code / Q&A | github, stackoverflow, askubuntu, superuser |
| Reference | wikipedia, mdn, mankier, arch linux wiki |
| Research | arxiv |
| Package registries | pypi, npm, crates.io, docker hub |

Every other built-in engine (about 350, including all image, video, music, social-media and torrent engines) is
dropped, and a new upstream engine stays off until it is added to the list. `search.safe_search` is `1`.

Some kept engines are off upstream and are re-enabled per engine: `google` and `bing` (`disabled: true`), `npm` and
`crates.io` (`disabled: true`), and `mojeek` and `startpage` (`inactive: true`, over their proof-of-work CAPTCHAs).
The last two get `weight: 0.5`, since they come back empty more often.

**Known gap**: upstream SearXNG has no Maven Central engine (its registry engines are `pypi`, `npm`, `crates.io`,
`hex`, `rubygems` and `packagist`). Adding one means a new `searx/engines/*.py` module.

## Settings layering

SearXNG loads one user settings file, the one `SEARXNG_SETTINGS_PATH` points at (a file, or a folder holding
`settings.yml`), merged onto its own `searx/settings.yml` through `use_default_settings`. The committed template
(`pleochroic/settings.yml`) must not hold the residential proxy's credentials, so `pleochroic/merge_settings.py`
deep-merges it with a private, root-owned overlay at deploy time, using PyYAML, which SearXNG already depends on.

```
pleochroic/settings.yml                       public, committed, no secrets
        +
/etc/pleochroic-search/settings.private.yml   root-owned, 0640, the real proxy URL, not in git
        =  merge_settings.py  =>
/run/pleochroic-search/settings.yml           regenerated on every service start
```

`ExecStartPre` in `pleochroic-search.service` runs the merge into the systemd `RuntimeDirectory=`
(`/run/pleochroic-search`, tmpfs, owned by the service user, removed on stop), and `ExecStart` launches SearXNG with
`SEARXNG_SETTINGS_PATH` set to the result. So:

- editing `/etc/pleochroic-search/settings.private.yml` and running `systemctl restart pleochroic-search` rotates the
  proxy credentials;
- `install.sh` overwrites `/etc/pleochroic-search/settings.yml` (the deployed public template) on every run, and
  creates `settings.private.yml` only when it is missing;
- without `settings.private.yml`, `merge_settings.py` writes the public settings unchanged, with no proxy.

`server.secret_key` comes from the `SEARXNG_SECRET` environment variable, which SearXNG's
`searx/settings_defaults.py` reads and which overrides any settings file. The unit loads it from
`EnvironmentFile=/etc/pleochroic-search/secrets.env` (root-owned, `0600`), which systemd reads as root before
dropping to the service user.

## Deployment

Target: Ubuntu 24.04, Python 3.10 or later (`setup.py`'s requirement; Ubuntu 24.04 ships 3.12).

```sh
git clone --recurse-submodules https://github.com/Pleochroic-Ltd/standalone-service.git
cd standalone-service/search
sudo pleochroic/install.sh
```

`install.sh` can be re-run. It installs OS packages, creates the system user `pleochroic-search`, syncs the code to
`/opt/pleochroic-search/release`, builds a venv at `/opt/pleochroic-search/venv` from `requirements.txt` and
`requirements-server.txt` (which pins [Granian](https://github.com/emmett-framework/granian)), lays out
`/etc/pleochroic-search/` (`0750`, `root:pleochroic-search`), and installs the systemd unit with `daemon-reload`.

It does not start the service. After the first run:

1. Put the real proxy URL in `/etc/pleochroic-search/settings.private.yml` (`settings.private.example.yml` shows the
   key and format).
2. `systemctl enable --now pleochroic-search`
3. `curl -s '127.0.0.1:8888/search?q=test&format=json'`
4. `journalctl -u pleochroic-search -f`

### Granian

SearXNG's official container (`container/Dockerfile`, `container/entrypoint.sh`) runs `granian searx.webapp:app`,
and `requirements-server.txt` pins `granian` for it. Its docs still describe uWSGI as the main path for other
installs, through Debian's packaged `uwsgi` with an Emperor/vassal layout (`utils/searxng.sh`). The fork runs Granian
the way the container does (`GRANIAN_INTERFACE=wsgi`, `searx.webapp:app`): it fits a single systemd unit, and it
ships prebuilt wheels.

### No Valkey

The rate limiter and its botdetection middleware are off: `server.limiter: false` and `server.public_instance: false`
(upstream's default, made explicit). With both false, `initialize()` in `searx/limiter.py` returns at once and never
opens Valkey, so SearXNG serves `/search?format=json` without a Valkey instance. The log shows one harmless warning,
`WARNING:searx.botdetection.config: missing config file: …/limiter.toml`.

### Systemd hardening

`pleochroic-search.service` sets `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateDevices`, `ProtectHome`, no
capabilities, `SystemCallFilter=@system-service`, `RestrictNamespaces` and
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, among others; the unit file has the full list.
`MemoryDenyWriteExecute` is off: `curl_cffi`, used for every outgoing engine request, loads `cffi`-based native
extensions that have not been tested under it on Linux. To try it, enable it on the server and watch the journal
for `SIGSYS` kills.

## AGPL obligation

This is a modified AGPL-3.0-or-later work offered as a network service, and AGPL §13 makes no exception for an
internal network. Pleochroic must offer anyone who interacts with the running service this fork's corresponding
source.

**Open item**: the footer's "Source code" link (`searxng_git_url`) comes from `GIT_URL` in `searx/version.py`, read
from the remote the checked-out branch tracks. `pleochroic` tracks `origin`, this fork
(https://github.com/Pleochroic-Ltd/pleochroic-search), but `install.sh` copies the tree without `.git`, so the
deployed instance shows no source link yet. Freeze the version information at install time before the service is
offered to anyone beyond the one Node process that calls it.

## Rebasing on upstream

```sh
git fetch origin                 # origin is upstream searxng/searxng
git checkout pleochroic
git rebase origin/master
```

The diff from upstream is a few template lines, one default setting, four test assertions, one call in
`searx/search/__init__.py`, `searx/pleochroic_proxy.py` with its test, and the new files under `pleochroic/`, so
conflicts are rare. `keep_only` matches engine names, so a rebase never enables a new upstream
engine on its own.

## Running locally

With Python 3.11 and no proxy:

```sh
python3.11 -m venv /tmp/pleochroic-search-venv
/tmp/pleochroic-search-venv/bin/pip install -r requirements.txt -r requirements-server.txt
python3 pleochroic/merge_settings.py pleochroic/settings.yml \
    /path/to/nonexistent-settings.private.yml /tmp/merged-settings.yml
SEARXNG_SETTINGS_PATH=/tmp/merged-settings.yml SEARXNG_SECRET=<random> \
GRANIAN_INTERFACE=wsgi GRANIAN_HOST=127.0.0.1 GRANIAN_PORT=28891 \
PYTHONPATH="$(pwd)" /tmp/pleochroic-search-venv/bin/granian searx.webapp:app
```

Without a proxy some engines (`duckduckgo`, `google`, `mojeek`) come back in `unresponsive_engines` with CAPTCHA or
access-denied errors; on the server every engine request goes through the proxy.

`yamllint --strict` (with SearXNG's `.yamllint.yml`) covers `searx/settings.yml`, `pleochroic/settings.yml` and
`pleochroic/settings.private.example.yml`. SearXNG's unit tests (`tests/unit`, run with `nose2` as `make test.unit`
does) pass, 359 of 359. `tests/unit/test_engine_tineye.py` imports `requests`, which upstream's requirements files
leave out, so it needs `requests` installed separately.
