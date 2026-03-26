ssh into your pi
git clone https://github.com/gilboash/fpv-field-access.git
cd fpv-field-access
chmod 777 setup.sh
./setup.sh 

for manual start:
python3 app.py


connect sd card reader to the USB port on your PI

now connected from your mobile / pc web browser to the target ip port 5000 (https://IP:5000)





for automatic start every boot (add to setup?):
sudo nano /etc/systemd/system/picam.service

paste:
[Unit]
Description=FPV Field Access
After=network.target

[Service]
ExecStart=/bin/bash /home/naco/fpv-field-access/start.sh
WorkingDirectory=/home/naco/fpv-field-access
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target

save

sudo systemctl daemon-reload
sudo systemctl enable picam
sudo systemctl start picam


picam.conf on the bootfs/ sd card should be either
mode=station

or

mode=hotspot
ssid=PiCam
password=picam1234