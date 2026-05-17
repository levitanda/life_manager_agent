#!/bin/bash
# One-time setup script for EC2 Ubuntu instance.
# Run as: bash server_setup.sh
set -e

REPO_URL="https://github.com/YOUR_USERNAME/life-agent.git"
APP_DIR="$HOME/life-agent"

echo "=== Installing system packages ==="
sudo apt-get update -q
sudo apt-get install -y python3-venv python3-pip git

echo "=== Cloning repository ==="
if [ -d "$APP_DIR" ]; then
  echo "Directory already exists, pulling latest..."
  cd "$APP_DIR" && git pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "=== Creating virtual environment ==="
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

echo ""
echo "=== MANUAL STEPS REQUIRED ==="
echo "From your LOCAL machine, run:"
echo "  scp -i your-key.pem .env google_credentials.json google_token.json ubuntu@YOUR_EC2_IP:~/life-agent/"
echo ""

echo "=== Installing systemd service ==="
sudo cp "$APP_DIR/life-agent.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable life-agent

echo ""
echo "=== Installing Cloudflare Tunnel (for Alice HTTPS webhook) ==="
curl -L --output /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb

echo ""
echo "=== Cloudflare Tunnel Setup ==="
echo "1. Go to https://one.dash.cloudflare.com → Zero Trust → Networks → Tunnels"
echo "2. Create a tunnel, copy the token"
echo "3. Run: sudo cloudflared service install <YOUR_TOKEN>"
echo "4. In the tunnel dashboard, add a Public Hostname:"
echo "   Subdomain: life-agent | Domain: your-domain.com | Service: http://localhost:5000"
echo ""
echo "=== Done! After copying secrets, start the service: ==="
echo "  sudo systemctl start life-agent"
echo "  sudo systemctl status life-agent"
