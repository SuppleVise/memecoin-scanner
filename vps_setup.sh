#!/bin/bash
# One-time setup on a fresh Ubuntu/Debian VPS (Hetzner, Oracle, ...). Run as root:
#   curl -fsSL https://raw.githubusercontent.com/SuppleVise/memecoin-scanner/main/vps_setup.sh | bash -s -- YOUR-NTFY-TOPIC
# Installs the scanner in /opt/mmfx and runs every mode as a service that restarts on crash and
# after reboot. Paper only: no wallet, no keys, no real orders. Alerts are titled "[vps <mode>]".
set -euo pipefail
TOPIC="${1:?give your ntfy topic as the first argument}"
MODES="moonshot early turbo gamble"
apt-get update -qq && apt-get install -y -qq python3 curl git >/dev/null
id mmfx >/dev/null 2>&1 || useradd --system --home /opt/mmfx --shell /usr/sbin/nologin mmfx
if [ -d /opt/mmfx/.git ]; then git -C /opt/mmfx pull -q; else git clone -q https://github.com/SuppleVise/memecoin-scanner /opt/mmfx; fi
echo "$TOPIC" > /opt/mmfx/ntfy_topic.txt
chown -R mmfx:mmfx /opt/mmfx
delay=0
for m in $MODES; do
  cat > /etc/systemd/system/mmfx-$m.service <<UNIT
[Unit]
Description=MMFX paper scanner ($m)
After=network-online.target
Wants=network-online.target

[Service]
User=mmfx
WorkingDirectory=/opt/mmfx
ExecStartPre=/bin/sleep $delay
ExecStart=/usr/bin/python3 -u run.py --mode $m --label vps
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
  delay=$((delay + 15))   # stagger so they don't hit GeckoTerminal at the same moment
done
systemctl daemon-reload
for m in $MODES; do systemctl enable --now -q mmfx-$m; done
sleep 5
systemctl --no-pager --lines=0 status $(for m in $MODES; do echo mmfx-$m; done) | grep -E "mmfx-|Active"
echo
echo "Done. Live log:   journalctl -fu mmfx-gamble     (Ctrl+C leaves it running)"
echo "Update code:      bash /opt/mmfx/vps_setup.sh $TOPIC"
