#!/usr/bin/env bash
# Pleochroic Search — install/update script for Ubuntu 24.04.
#
# Idempotent: safe to re-run after a `git pull` to pick up fork updates, or
# after editing the systemd unit template. It never touches
# /etc/pleochroic-search/settings.private.yml or secrets.env once they
# exist — those are the two operator-owned files with real secrets in them.
#
# What it does:
#   1. installs OS packages (python3, venv, pip, rsync)
#   2. creates the system user/group `pleochroic-search`
#   3. syncs this checkout into /opt/pleochroic-search/release
#   4. creates/updates a venv at /opt/pleochroic-search/venv and installs
#      SearXNG's own requirements + requirements-server.txt (granian)
#   5. lays out /etc/pleochroic-search (0750) with the public settings
#      template, and scaffolds settings.private.yml / secrets.env only if
#      they don't already exist
#   6. installs the systemd unit and runs `daemon-reload`
#
# It does NOT start or enable the service — see the printed summary at the
# end for the manual steps (fill in the proxy credentials first).
#
# Usage: sudo ./install.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must be run as root (sudo ./install.sh)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # search/ (the SearXNG checkout root)

PREFIX=/opt/pleochroic-search
RELEASE_DIR="$PREFIX/release"
VENV_DIR="$PREFIX/venv"
CONF_DIR=/etc/pleochroic-search
SERVICE_USER=pleochroic-search
SERVICE_GROUP=pleochroic-search
UNIT_PATH=/etc/systemd/system/pleochroic-search.service

log() { echo "[pleochroic-search install] $*"; }

if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
        log "warning: tested on Ubuntu 24.04, detected ${PRETTY_NAME:-unknown} — continuing anyway"
    fi
fi

log "1/6 installing OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip rsync ca-certificates openssl >/dev/null

log "2/6 system user/group ($SERVICE_USER)"
if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$PREFIX" \
        --shell /usr/sbin/nologin --no-create-home "$SERVICE_USER"
fi

log "3/6 syncing code to $RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
rsync -a --delete \
    --exclude='.git' \
    --exclude='pleochroic/settings.private.yml' \
    "$REPO_ROOT"/ "$RELEASE_DIR"/

log "4/6 python venv + requirements"
if [ ! -x "$VENV_DIR/bin/python3" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip wheel >/dev/null
"$VENV_DIR/bin/pip" install \
    -r "$RELEASE_DIR/requirements.txt" \
    -r "$RELEASE_DIR/requirements-server.txt" >/dev/null

chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PREFIX"

log "5/6 config in $CONF_DIR"
mkdir -p "$CONF_DIR"
chown root:"$SERVICE_GROUP" "$CONF_DIR"
chmod 0750 "$CONF_DIR"

# Public template: always synced from the repo, never hand-edited in place.
install -o root -g "$SERVICE_GROUP" -m 0640 \
    "$RELEASE_DIR/pleochroic/settings.yml" "$CONF_DIR/settings.yml"

# Private overlay: scaffolded from the example ONLY if missing. Real
# credentials must be filled in by hand — see settings.private.example.yml
# and pleochroic/README.md ("Settings layering").
if [ ! -f "$CONF_DIR/settings.private.yml" ]; then
    install -o root -g "$SERVICE_GROUP" -m 0640 \
        "$RELEASE_DIR/pleochroic/settings.private.example.yml" "$CONF_DIR/settings.private.yml"
    log "created $CONF_DIR/settings.private.yml from the example template — EDIT IT with the real proxy URL before starting the service"
else
    log "$CONF_DIR/settings.private.yml already exists, leaving it untouched"
fi

# SEARXNG_SECRET: generated once if missing, root-only (systemd reads this
# file as root via EnvironmentFile=, before dropping to the service user —
# it never needs to be group-readable).
if [ ! -f "$CONF_DIR/secrets.env" ]; then
    {
        echo "# Pleochroic Search — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "SEARXNG_SECRET=$(openssl rand -hex 32)"
    } > "$CONF_DIR/secrets.env"
    chown root:root "$CONF_DIR/secrets.env"
    chmod 0600 "$CONF_DIR/secrets.env"
    log "generated $CONF_DIR/secrets.env with a fresh SEARXNG_SECRET"
else
    log "$CONF_DIR/secrets.env already exists, leaving it untouched"
fi

log "6/6 systemd unit"
install -o root -g root -m 0644 \
    "$RELEASE_DIR/pleochroic/pleochroic-search.service" "$UNIT_PATH"
systemctl daemon-reload

log "done."
cat <<EOF

Next steps:
  1. Edit $CONF_DIR/settings.private.yml with the real residential proxy URL
     (see the comments in that file and pleochroic/README.md).
  2. systemctl enable --now pleochroic-search
  3. curl -s '127.0.0.1:8888/search?q=test&format=json' | head -c 300
  4. journalctl -u pleochroic-search -f   # to watch it come up / debug

Re-running this script later (e.g. after \`git pull\`) updates the code,
venv and the public settings.yml template, and leaves settings.private.yml
and secrets.env alone.
EOF
