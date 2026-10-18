# XIAO ESP32S3 Sense - Setup Guide

## What you need
- Seeed XIAO ESP32S3 **Sense** (with the camera board clipped on)
- USB-C data cable, laptop with Arduino IDE 2.x
- The hub running on the laptop (`python -m uvicorn app:app --host 0.0.0.0 --port 8000`)
- Laptop and XIAO on the **same WiFi** (2.4 GHz only - ESP32 can't use 5 GHz; a phone hotspot works)

## One-time Arduino IDE setup
1. File -> Preferences -> "Additional boards manager URLs": add
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
2. Tools -> Board -> Boards Manager -> search **esp32** -> install "esp32 by Espressif Systems".
3. Tools -> Board -> esp32 -> **XIAO_ESP32S3**.
4. Tools -> **PSRAM -> OPI PSRAM** (essential; without it the camera fails to start).
5. Tools -> USB CDC On Boot -> Enabled.

## Per-camera setup
1. Open the dashboard (`http://<laptop-ip>:8000`) -> Admin -> add a Machine, then add a Camera assigned to it. Copy the key (e.g. `CAM-3F9A1C`).
2. In `firmware/xiao_esp32s3/`, copy `secrets.h.example` to `secrets.h` and fill in: WiFi name, WiFi password, HUB_URL (laptop WiFi IP, e.g. `http://10.180.64.143:8000`), API_KEY. Then open `xiao_esp32s3.ino` in Arduino IDE (the secrets.h tab appears beside it).
3. Select the XIAO's COM port, click Upload. If upload fails: hold the **B** (boot) button, tap **R** (reset), release B, upload again.
4. Open Serial Monitor at 115200. You should see "Connected. Camera IP: ...". The dashboard card turns **Online** within ~2 s.

## Using it
1. **Set the reference:** put a good part in front of the camera, press **Capture photo** on the camera card. Within ~3 s a window pops up with the photo -> **Use as reference**.
2. **Test once:** put another part in view, press Capture photo -> **Test against reference**.
3. **Automatic:** tick **Auto-test every [N] s** on the card (needs a reference). The camera now takes and sends a photo every N seconds on its own; results, alerts and history appear as normal. Untick to stop.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Camera init failed 0x105/0x20004` | PSRAM not set to OPI PSRAM, or camera board not fully clicked in. |
| `Cannot reach hub (-1)` | Wrong HUB_URL, different WiFi, or Windows Firewall blocking port 8000 (allow Python on Private networks). |
| `unknown API key` | Key doesn't match Admin -> Cameras; recopy it. |
| Card stays Offline | Camera not polling - check Serial Monitor. |
| "Could not measure the part" on Use as reference | Part not in view / poor contrast; use a plain background and good light. |
| Image dark or blurry | Add light, wipe lens, focus by turning the lens ring on the OV2640 module. |
