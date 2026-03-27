#!/bin/bash

CONFIG="/boot/firmware/picam.conf"
LOG="/home/naco/fpv-field-access/picam.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

# defaults
SSID="PiCam"
PASSWORD="picam1234"

# read config
if [ -f "$CONFIG" ]; then
    while IFS='=' read -r key value; do
        key=$(echo "$key" | tr -d ' ')
        value=$(echo "$value" | tr -d ' \r')
        case "$key" in
            ssid)     SSID="$value" ;;
            password) PASSWORD="$value" ;;
        esac
    done < "$CONFIG"
fi

log "Starting hotspot: SSID=$SSID"

# clean up any previous hotspot
sudo nmcli connection delete picam-hotspot 2>/dev/null

# create hotspot
sudo nmcli connection add type wifi ifname wlan0 con-name picam-hotspot autoconnect no \
    ssid "$SSID" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv4.addresses 192.168.4.1/24 \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASSWORD"

sudo nmcli device disconnect wlan0
sleep 3
sudo nmcli connection up picam-hotspot
sleep 2

AP_IP=$(ip addr show wlan0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
log "Hotspot up at $AP_IP"

# start app
log "Starting FPV Field Access..."
cd /home/naco/fpv-field-access

# with this:
exec gunicorn --workers 1 --bind 0.0.0.0:5000 --timeout 300 app:app >> "$LOG" 2>&1
