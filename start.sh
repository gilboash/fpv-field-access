#!/bin/bash

CONFIG="/boot/firmware/picam.conf"
LOG="/home/naco/fpv-field-access/picam.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

# defaults
MODE="station"
SSID="PiCam"
PASSWORD="picam1234"

# read config from SD card if it exists
if [ -f "$CONFIG" ]; then
    log "Found picam.conf on SD card"
    while IFS='=' read -r key value; do
        key=$(echo "$key" | tr -d ' ')
        value=$(echo "$value" | tr -d ' \r')
        case "$key" in
            mode)     MODE="$value" ;;
            ssid)     SSID="$value" ;;
            password) PASSWORD="$value" ;;
        esac
    done < "$CONFIG"
else
    log "No picam.conf found, defaulting to station mode"
fi

log "Mode: $MODE"

if [ "$MODE" = "hotspot" ]; then
    log "Starting hotspot: SSID=$SSID"

    # remove any existing hotspot connection
    nmcli connection delete picam-hotspot 2>/dev/null

    # create hotspot
    nmcli connection add type wifi ifname wlan0 con-name picam-hotspot autoconnect no \
        ssid "$SSID" \
        802-11-wireless.mode ap \
        802-11-wireless.band bg \
        ipv4.method shared \
        ipv4.addresses 192.168.4.1/24 \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$PASSWORD"

    # disconnect from current wifi and bring up hotspot
    nmcli device disconnect wlan0
    sleep 2
    nmcli connection up picam-hotspot

    log "Hotspot started at 192.168.4.1"

else
    log "Station mode — using existing WiFi connection"
fi

# wait for network to settle
sleep 3

# start the app
log "Starting FPV Field Access app..."
cd /home/naco/fpv-field-access
exec /usr/bin/python3 app.py >> "$LOG" 2>&1