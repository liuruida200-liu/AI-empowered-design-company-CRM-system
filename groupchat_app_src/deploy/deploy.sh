#!/bin/bash
# Design CRM — Server Setup Script
# Run once on a fresh Ubuntu 22.04 / Debian 12 server as root
# Usage: bash deploy.sh your-domain.com

set -euo pipefail

DOMAIN="${1:?Usage: bash deploy.sh your-domain.com}"
INSTALL_DIR="/opt/designcrm"
APP_USER="designcrm"

echo "==> [1/7] Installing system packages"
apt-get update -q
apt-get install -y python3 python3-pip python3-venv nginx certbot \
    python3-certbot-nginx mysql-server git

echo "==> [2/7] Creating app user and directory"
id "$APP_USER" &>/dev/null || useradd -r -s /bin/false "$APP_USER"
mkdir -p "$INSTALL_DIR"
# Copy project files to install dir (run from repo root)
cp -r . "$INSTALL_DIR/"
chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"

echo "==> [3/7] Setting up Python virtual environment"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/groupchat_app_src/backend/requirements.txt"

echo "==> [4/7] Configuring MySQL"
mysql -e "CREATE DATABASE IF NOT EXISTS groupchat CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER IF NOT EXISTS 'chatuser'@'localhost' IDENTIFIED BY 'CHANGE_THIS_PASSWORD';"
mysql -e "GRANT ALL PRIVILEGES ON groupchat.* TO 'chatuser'@'localhost'; FLUSH PRIVILEGES;"
echo "  !! Set a real password in MySQL and update .env DATABASE_URL"

echo "==> [5/7] Installing systemd service"
# Patch install path in service file
sed "s|/opt/designcrm|$INSTALL_DIR|g" \
    "$INSTALL_DIR/groupchat_app_src/deploy/designcrm.service" \
    > /etc/systemd/system/designcrm.service
systemctl daemon-reload
systemctl enable designcrm

echo "==> [6/7] Configuring Nginx + SSL"
sed "s/your-domain.com/$DOMAIN/g" \
    "$INSTALL_DIR/groupchat_app_src/deploy/nginx.conf" \
    > /etc/nginx/sites-available/designcrm
ln -sf /etc/nginx/sites-available/designcrm /etc/nginx/sites-enabled/designcrm
rm -f /etc/nginx/sites-enabled/default
nginx -t

# Obtain Let's Encrypt certificate
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN"
systemctl reload nginx

echo "==> [7/7] Starting app and seeding database"
systemctl start designcrm
sleep 3   # wait for uvicorn to finish init_db()

echo "  Seeding production capabilities..."
cd "$INSTALL_DIR/groupchat_app_src/backend"
"$INSTALL_DIR/venv/bin/python" seed.py && echo "  ✓ Seed complete" || echo "  ⚠ Seed failed — run manually: python seed.py"

echo ""
echo "  Before starting, edit $INSTALL_DIR/groupchat_app_src/.env:"
echo "    DATABASE_URL   — use the MySQL password you set above"
echo "    JWT_SECRET     — set a long random string"
echo "    LLM_API_BASE   — your LLM endpoint"
echo ""
echo "  Then run:"
echo "    sudo systemctl start designcrm"
echo "    sudo journalctl -u designcrm -f"
echo ""
echo "  Done! App will be available at: https://$DOMAIN"
