#!/usr/bin/env bash
# One-off provisioning script for the FastAPI sidecar (dalev.click).
#
# Prerequisites (do these manually first):
#   1. Route 53 → A record dalev.click → 3.67.8.7 (already exists if user confirmed).
#   2. AWS Lightsail firewall: ensure ports 80 and 443 are open.
#
# Run on the EC2 instance as the `ubuntu` user:
#   bash infra/setup_web_sidecar.sh

set -euo pipefail

REPO_DIR="/home/ubuntu/life-agent"
DOMAIN="dalev.click"
EMAIL="dalevitan17@gmail.com"

echo "== Installing nginx and certbot =="
sudo apt-get update -qq
sudo apt-get install -y -qq nginx certbot python3-certbot-nginx

echo "== Installing nginx site config =="
sudo cp "${REPO_DIR}/infra/nginx.conf" "/etc/nginx/sites-available/${DOMAIN}"
sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "== Installing life-agent-web.service systemd unit =="
sudo cp "${REPO_DIR}/infra/life-agent-web.service" /etc/systemd/system/life-agent-web.service
sudo systemctl daemon-reload
sudo systemctl enable life-agent-web.service
sudo systemctl restart life-agent-web.service
sleep 2
sudo systemctl is-active life-agent-web.service

echo "== Quick sanity check (HTTP) =="
curl -fsS "http://${DOMAIN}/health" && echo

echo "== Provisioning Let's Encrypt cert =="
sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect

echo "== Final HTTPS check =="
curl -fsS "https://${DOMAIN}/health" && echo
echo "Done. FastAPI sidecar live at https://${DOMAIN}"
