# EdgeHealth Pi Gateway

Flask + SQLite + APScheduler service. Ingests health readings from the Android
relay, runs hybrid anomaly detection, pushes daily summaries to the cloud.

## Install on Pi

```bash
ssh nick@10.0.0.153
sudo apt update && sudo apt install -y python3-venv git
git clone <YOUR-REPO> ~/edgehealth
cd ~/edgehealth/pi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python db.py
```

## Configure

Set these environment variables (or edit `edgehealth.service`):

```bash
export EDGEHEALTH_HMAC="<paste from Render Environment tab>"
export EDGEHEALTH_CLOUD_URL="https://edgehealth-cloud-XXXX.onrender.com"
export EDGEHEALTH_NTFY_TOPIC="edgehealth-nick-2026"   # pick something unique
```

Subscribe to ntfy on phone: install ntfy app, subscribe to your topic.

## Run

Foreground (testing):
```bash
python app.py
```

As a service:
```bash
sudo cp edgehealth.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edgehealth
sudo journalctl -u edgehealth -f
```

## Smoke test

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"readings":[{"ts":"2026-04-25T10:00:00","metric":"hr","value":72}]}'
curl http://localhost:8000/status
```
