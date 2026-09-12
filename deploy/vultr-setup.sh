#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 Vultr instance to run the Synesis API.
#
#   ssh root@<your-vultr-ip>
#   curl -fsSL https://raw.githubusercontent.com/veriivy/synesis/main/deploy/vultr-setup.sh -o setup.sh
#   bash setup.sh <your-vultr-ip>
#
# Takes a few minutes. Afterwards you still have to write /opt/synesis/.env by
# hand — API keys are the one thing that never travels through a script or a
# repo.
#
# NOT TESTED against a real Vultr box. Read it before you run it; if a step
# fails, the failure will be obvious and local to that step.
set -euo pipefail

IP="${1:?usage: bash setup.sh <public-ip>}"
REPO="${2:-https://github.com/veriivy/synesis.git}"
APP_DIR=/opt/synesis
DOMAIN="${IP}.sslip.io"

echo "==> installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ufw \
	debian-keyring debian-archive-keyring apt-transport-https

echo "==> installing caddy"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
	| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
	> /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq
apt-get install -y -qq caddy

echo "==> creating the service user"
# The API runs as an unprivileged user. It shells out to git and writes files
# that a language model chose the contents of — it does not get to be root.
id -u synesis &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin synesis

echo "==> cloning $REPO"
if [ -d "$APP_DIR/.git" ]; then
	git -C "$APP_DIR" pull --ff-only
else
	git clone --quiet "$REPO" "$APP_DIR"
fi

echo "==> python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/server/requirements.txt"
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR/engine"

echo "==> .env"
if [ ! -f "$APP_DIR/.env" ]; then
	cp "$APP_DIR/.env.example" "$APP_DIR/.env"
	echo "    created from .env.example — YOU MUST ADD YOUR API KEYS"
fi

echo "==> permissions"
# The service user needs to write per-room clones into workspaces/.
mkdir -p "$APP_DIR/workspaces"
chown -R synesis:synesis "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> systemd"
cp "$APP_DIR/deploy/synesis.service" /etc/systemd/system/synesis.service
systemctl daemon-reload
systemctl enable --now synesis

echo "==> caddy for https://$DOMAIN"
sed "s/REPLACE_ME.sslip.io/${DOMAIN}/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
systemctl reload caddy || systemctl restart caddy

echo "==> firewall"
# 8000 is deliberately NOT opened. uvicorn listens on localhost; everything
# from outside arrives through Caddy on 443.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

cat <<EOF

================================================================
Backend is up.

  API      https://${DOMAIN}
  health   https://${DOMAIN}/health

Still to do, by hand:

  1. nano ${APP_DIR}/.env      <- add ANTHROPIC_API_KEY, OPENAI_API_KEY,
                                  IFM_API_KEY. Then:
                                  systemctl restart synesis

  2. Give Vercel this as NEXT_PUBLIC_API_BASE:
       https://${DOMAIN}

  3. Once Vercel gives you a URL, put it in CORS_ORIGINS in .env and
     restart, or the deployed frontend cannot call this server at all.

Logs:
  journalctl -u synesis -f
  journalctl -u caddy -f
================================================================
EOF
