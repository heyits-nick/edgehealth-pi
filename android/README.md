# EdgeHealth Android Relay

Foreground service that polls Health Connect every 60s and POSTs new HR /
SpO2 / steps readings to the Pi gateway over the local WiFi.

## Build & install

1. Open **Android Studio** → **File → Open** → select this `android/` folder.
2. Wait for Gradle sync (~5 min first time, downloads SDK + deps).
3. Plug phone via USB, enable USB debugging, **Always allow** when prompted.
4. Toolbar shows your phone in the device dropdown.
5. Click **Run** (green ▶). App installs and launches.

## First-run setup on phone

1. Tap **Grant Health Connect permissions** → grant Heart rate / SpO2 / Steps.
2. Confirm Pi URL (default `http://10.0.0.153:8000`).
3. Tap **Start relay**. Persistent notification appears.
4. Go to Settings → Battery → EdgeHealth Relay → set to **Unrestricted** so Doze
   doesn't kill the service when screen is off.

## Verify

On the Pi, watch ingest:

```bash
journalctl -u edgehealth -f          # if running as service
# or watch /status
watch -n 5 "curl -s http://localhost:8000/status"
```

`readings_24h` should climb every 60s once the watch syncs to Health Connect.

## Notes

- Cleartext HTTP to `10.0.0.153` is whitelisted via
  `res/xml/network_security_config.xml`. If your Pi's IP differs, edit that file
  and rebuild.
- The app does **not** talk to the cloud. All readings go to the Pi only.
