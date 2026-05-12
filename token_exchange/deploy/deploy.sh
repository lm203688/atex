#!/bin/bash
# ATEX API Server Deployment Script
# For Alibaba Cloud ECS (Ubuntu 22.04/24.04)

set -e

echo "=== ATEX API Server Deployment ==="

# 1. Install dependencies
echo "[1/5] Installing Python3 and dependencies..."
sudo apt update && sudo apt install -y python3 python3-pip

# 2. Create ATEX directory
echo "[2/5] Setting up ATEX directory..."
sudo mkdir -p /opt/atex
sudo cp -r . /opt/atex/
cd /opt/atex

# 3. Create systemd service
echo "[3/5] Creating systemd service..."
sudo tee /etc/systemd/system/atex.service > /dev/null << 'SERVICE'
[Unit]
Description=ATEX Agent Service Exchange API
After=network.target

[Service]
Type=simple
User=atex
Group=atex
WorkingDirectory=/opt/atex/token_exchange
ExecStart=/usr/bin/python3 /opt/atex/token_exchange/api/server.py 8420
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

# 4. Create atex user and set permissions
echo "[4/5] Creating atex user..."
sudo useradd -r -s /bin/false atex 2>/dev/null || true
sudo chown -R atex:atex /opt/atex

# 5. Start the service
echo "[5/5] Starting ATEX service..."
sudo systemctl daemon-reload
sudo systemctl enable atex
sudo systemctl start atex

echo ""
echo "=== Deployment Complete ==="
echo "ATEX API running on: http://<YOUR_ECS_IP>:8420"
echo "Test: curl http://<YOUR_ECS_IP>:8420/api/v1/status"
echo ""
echo "Don't forget to open port 8420 in your ECS Security Group!"
