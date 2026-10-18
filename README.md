# VisionQC

**Vision-based automatic dimension inspection.** Cameras mounted at a machine's output photograph every part and send it wirelessly to a central hub. The hub measures the part with OpenCV, compares it to a reference ("golden sample") photo, scores it as a **% error**, explains *why* a part failed, and shows everything on a live dashboard with alerts.

Final-year RBL project, Mechatronics Engineering, TCET Mumbai.

## Features
- **Admin-managed** - add machines and cameras from the dashboard; reassign cameras between machines any time.
- **Reference-photo comparison** - the first good part defines the standard; later parts are scored as % error against the machine's tolerance. No calibration needed.
- **Wireless cameras** - Seeed **XIAO ESP32S3 Sense** (main target), AI-Thinker ESP32-CAM, a laptop webcam, or just a phone browser.
- **Remote capture + timer** - press *Capture photo* on the dashboard to grab a reference; switch on *Auto-test every N s* and the camera inspects on its own.
- **Explainable diagnosis** - tolerance check, noise-aware drift detection, isolated-defect detection, and a *"check the camera first"* rule when length and width shift together. No black-box model.
- **Millimetres + capability** - enter the reference part's real size and readings are also shown in mm; the card shows a **Cpk** badge so you know whether the set-up can really hold the tolerance.
- **Sensor-triggered shots** - a photo-eye on the XIAO's D1 pin fires one photo per part (timer mode kept for bench use). Empty frames are ignored.
- **Hardening** - optional admin password, escaped UI, upload limits, image retention, camera-offline alerts.
- **Live alerts** - toast, beep and desktop notifications; every inspection image is stored for cross-checking.

## Architecture
```
 XIAO ESP32S3 / ESP32-CAM / phone / webcam
        |  Wi-Fi: polls hub for orders, uploads JPEGs (per-camera API key)
        v
 +------------------ Hub (FastAPI + SQLite) ------------------+
 |  vision.py       OpenCV: Canny -> largest contour -> size   |
 |  diagnostics.py  % error vs reference, drift/defect rules   |
 |  database.py     machines, cameras, references, history     |
 +--------------------------+----------------------------------+
                            | WebSocket live push
                            v
                  Dashboard (browser): admin, live cards, alerts
```
The hub never connects *to* a camera - cameras call the hub (`GET /api/device/<key>/poll`), so it works behind any Wi-Fi or phone hotspot.

## Repository layout
| Path | What |
|---|---|
| `hub/` | FastAPI server, vision, diagnostics, SQLite, dashboard (`static/`) |
| `firmware/xiao_esp32s3/` | XIAO ESP32S3 Sense firmware (Arduino) |
| `firmware/esp32cam/` | AI-Thinker ESP32-CAM firmware (interval upload only) |
| `machine_node/` | Python client for a laptop + webcam |
| `tools/` | `simulate_floor.py`, `simulate_xiao.py` - demo with no hardware |
| `tests/` | pytest suite (vision, diagnostics, API) |
| `docs/` | User guide, hardware & wiring, XIAO setup, design review (`CRITIQUE.md`) |
| `deploy/` | systemd service file |

## Quick start
```bash
git clone https://github.com/Manas-36/VisionQC.git
cd VisionQC/hub
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
Open <http://localhost:8000> (not `0.0.0.0`). Phones/cameras on the same Wi-Fi use `http://<your-laptop-ip>:8000` (`ipconfig` / `ip addr`).

Or with Docker: `docker compose up --build`.

### Try it without hardware
With the hub running, in another terminal:
```bash
python tools/simulate_floor.py          # fake machines + drifting parts
# or pretend to be a XIAO camera (create a camera in Admin first):
python tools/simulate_xiao.py --api-key CAM-XXXXXX
```

### Use a real XIAO ESP32S3 Sense
1. Dashboard -> Admin -> add a machine, then a camera; copy its `CAM-XXXXXX` key.
2. In `firmware/xiao_esp32s3/` copy `secrets.h.example` to `secrets.h`, fill in Wi-Fi, hub URL and key.
3. Arduino IDE: board **XIAO_ESP32S3**, PSRAM **OPI PSRAM**, upload.
4. Press **Capture photo** on the camera card -> **Use as reference**. Then tick **Auto-test every N s**.

Full steps and troubleshooting: [docs/XIAO_SETUP_GUIDE.md](docs/XIAO_SETUP_GUIDE.md). Wiring and BOM: [docs/HARDWARE_AND_WIRING.md](docs/HARDWARE_AND_WIRING.md). Everything else: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## API (summary)
| Endpoint | Purpose |
|---|---|
| `POST /api/machines`, `/api/cameras` | Admin: add machines / cameras (camera returns its `api_key`) |
| `POST /api/cameras/{id}/capture` | Ask the camera for a preview photo |
| `POST /api/cameras/{id}/preview/use-as-reference` | Accept the preview as the reference |
| `POST /api/cameras/{id}/auto` | Auto-test on/off, `mode` = `timer`/`sensor`, interval |
| `GET /api/cameras/{id}/capability` | Spread, Cpk, gauge-repeatability numbers |
| `GET /api/hub-info` | The hub's LAN address to put in cameras |
| `GET /api/device/{key}/poll` | Camera asks for orders + settings |
| `POST /api/device/{key}/photo` | Camera uploads a preview photo |
| `POST /api/inspect` | Camera uploads a photo to be scored (`api_key` + `image`) |
| `GET /docs` | Interactive OpenAPI docs |

## Development
```bash
pip install -r requirements-dev.txt
pytest
```
CI runs the tests on every push. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `VISIONQC_ADMIN_PASSWORD` | *(unset = open)* | Require this password (HTTP Basic, any username) for the dashboard/admin API. **Set it.** |
| `VISIONQC_DATA_DIR` | `hub/data` | Database + images location |
| `VISIONQC_RETENTION_DAYS_PASS` / `_FAIL` | 30 / 180 | Days to keep inspection image files |

Windows: `set VISIONQC_ADMIN_PASSWORD=yourpassword` before starting uvicorn. Hardware, lighting and accuracy
limits: [docs/HARDWARE_AND_WIRING.md](docs/HARDWARE_AND_WIRING.md). Design review of what was wrong and what changed: [docs/CRITIQUE.md](docs/CRITIQUE.md).

## Limitations / future scope
- Measures one bounding box (length x width) per part. Millimetres come from one entered reference size (no lens-distortion model). Realistic tolerance with the XIAO + backlight is about +/-0.3 mm or looser - verify with the Cpk/repeatability check.
- Rules-based diagnosis; a CNN could add surface-defect detection.
- Single shared admin password, plain HTTP: run on a closed LAN or behind an HTTPS reverse proxy.
- The system reports; it does not reject parts or stop the machine.
- Alerts are on-page/browser only; email/SMS/webhooks are a natural extension.

## Team
Manas, Jha Pragati A, Pillai Brinda R - Mechatronics Engineering, TCET Mumbai.

## License
[MIT](LICENSE)
