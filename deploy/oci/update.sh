#!/usr/bin/env bash
#
# Redeploy the current branch and confirm the service came back.
#
#   sudo /opt/fpb/deploy/oci/update.sh
#
# Rolls back to the previous commit if the health check does not pass, so a bad
# pull leaves a running service rather than a dead one.

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/fpb}
APP_USER=${APP_USER:-fpb}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/api/health}

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

cd "$APP_DIR"

previous=$(sudo -u "$APP_USER" git rev-parse HEAD)
echo "current: $previous"

sudo -u "$APP_USER" git pull --ff-only
sudo -u "$APP_USER" .venv/bin/pip install --quiet --upgrade -r requirements.txt

echo "restarting"
systemctl restart fpb

# The app opens no provider connections at startup, so it is listening within a
# second or two. Twenty is slack for a slow ARM boot, not an expected wait.
for _ in $(seq 1 20); do
    if curl -sf --max-time 3 "$HEALTH_URL" >/dev/null; then
        echo "healthy at $(sudo -u "$APP_USER" git rev-parse --short HEAD)"
        # Worth an eye every deploy: a provider missing from this list is a
        # provider whose key or model is unset, and it fails silently.
        curl -s "$HEALTH_URL" | python3 -c \
            'import json,sys; d=json.load(sys.stdin); print("providers:", [p["name"]+"/"+p["model"] for p in d["providers"]]); print("access:", d["access_control"], "| persistence:", d["persistence"])'
        exit 0
    fi
    sleep 1
done

echo "did not come up — rolling back to $previous" >&2
sudo -u "$APP_USER" git reset --hard "$previous"
sudo -u "$APP_USER" .venv/bin/pip install --quiet -r requirements.txt
systemctl restart fpb
journalctl -u fpb -n 40 --no-pager >&2
exit 1
