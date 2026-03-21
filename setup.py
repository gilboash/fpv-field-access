#!/bin/bash
echo "Setting up FPV Field Access..."

sudo apt update
sudo apt install -y git ffmpeg python3-flask python3-pip

cd ~/fpv-field-access
mkdir -p work/thumbs

echo "Done. Run: python3 app.py"