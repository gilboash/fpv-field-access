#!/bin/bash
echo "=== Naco Media Box Setup ==="

# 1. Install dependencies
echo "Installing dependencies..."
sudo apt update
sudo apt install -y git ffmpeg python3-flask python3-pip
sudo apt install -y hostapd dnsmasq

# 2. Create work directories
echo "Creating work directories..."
mkdir -p ~/fpv-field-access/work/thumbs

# 3. Fix SD card mount


# 4. Disable dnsmasq (conflicts with hotspot)
echo "Disabling dnsmasq..."
sudo systemctl stop dnsmasq
sudo systemctl disable dnsmasq

# 5. Add nmcli sudoers rule
echo "Adding nmcli sudo permissions..."
echo "naco ALL=(ALL) NOPASSWD: /usr/bin/nmcli" | sudo tee /etc/sudoers.d/picam

# 6. Install systemd service
echo "Installing systemd service..."
sudo cp ~/fpv-field-access/picam.service /etc/systemd/system/picam.service
sudo systemctl daemon-reload
sudo systemctl enable picam
sudo apt install -y gunicorn

# 7. Create default picam.conf if not exists
if [ ! -f /boot/firmware/picam.conf ]; then
    echo "Creating default picam.conf..."
    sudo tee /boot/firmware/picam.conf << EOF
ssid=PiCam
password=picam1234
EOF
fi

echo "=== Setup complete. Rebooting in 5 seconds ==="
sleep 5
sudo reboot