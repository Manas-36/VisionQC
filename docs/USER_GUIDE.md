# VisionQC - User Guide

Admin-managed, multi-machine, multi-camera dimensional inspection. You add
machines and cameras from the dashboard -- no code editing needed to bring
a new station online. Every camera's quality standard is a real photo of
a known-good part (its "reference"/"golden sample"), not a typed-in
number -- every later photo from that camera is compared against it and
scored as a **percentage error**.

This is a real, runnable app, tested end to end with a floor simulator so
you can see it working before wiring up any camera.

## 1. How it works, in order

1. **Admin adds a machine** on the dashboard (Admin -> Machines -> Add
   machine): a name, a location, and a max error % (how much size
   variation is acceptable before a part is rejected).
2. **Admin adds a camera** (Admin -> Cameras -> Add camera) and assigns
   it to that machine. This generates an API key (`CAM-XXXXXX`) -- put
   that into the camera's config (see below) so the hub knows which
   photos come from which camera. Cameras can be reassigned to a
   different machine at any time, or added later as you wire up more
   stations -- nothing is hardcoded.
3. **A reference photo is captured on that camera**: a known-good
   "master" part is photographed once (via the dashboard upload, or
   straight from the camera itself). The hub measures it and stores that
   as the baseline for that specific camera. Each camera keeps its own
   reference, because two cameras -- even on the same machine -- sit at
   different distances/angles and see different pixel sizes for an
   identical part.
4. **From then on**, every photo that camera sends is measured and
   compared against its own reference -- the result is a % error (how
   far this part's width/height is from the reference sample), checked
   against that machine's max error %, with a PASS/FAIL decision and an
   explanation.
5. Every inspection (image + result) is stored, so you can cross-check
   any reading against the actual photo later.
6. If something's wrong, the dashboard raises it immediately: an on-page
   alert, a toast notification, a short alert tone, and (if you click
   "Enable alerts" once) a desktop notification -- even if that browser
   tab isn't focused.

## 2. Architecture

```
   Camera(s) at machine output           Central Hub (FastAPI)             Dashboard (browser)
  +-------------------------+          +----------------------+          +-----------------------+
  | ESP32-CAM (wireless)     |  photo   | vision.py             |  live    | Admin panel:           |
  |   -- or --               | -------> |  measures part size   |  push    |  add machines/cameras, |
  | laptop + webcam          |  + api   | diagnostics.py         | -------> |  set reference photos  |
  |  (node_client.py)        |   key    |  compares vs reference,|  (WS)    |                        |
  +-------------------------+          |  explains PASS/FAIL    |          | Live camera cards,     |
                                        | database.py             |          | alerts, history, images|
                                        |  stores every result    |          +-----------------------+
                                        +----------------------+
```

Cameras only ever know their own `api_key` -- everything about which
machine that maps to, what tolerance applies, and what the reference
looks like lives on the hub and can be changed from the dashboard
without touching a single camera.

## 3. Why comparison is % error, not typed-in millimetres

You don't need to measure anything with a ruler or calibrate pixels-to-mm
by hand. Since a given camera stays in the same position for both its
reference photo and every later photo, comparing pixel sizes directly and
expressing the difference as a percentage is both simpler and exactly
what was asked for: *"compare it with the actual product image we
uploaded earlier and give the percentage of error."* If a camera is ever
physically moved, just re-capture that camera's reference -- everything
else keeps working.

## 4. What "AI checks the product" means here

Rather than a black-box model, the hub runs an explainable statistical
process control (SPC) engine (`diagnostics.py`). For every new reading it
looks at that camera's recent history and works out:

1. **Is it in tolerance?** (checks both width and height error against
   the machine's max error %, using whichever is larger)
2. **Is this a one-off, or a pattern?** (a run of readings drifting the
   same direction -- a classic tool-wear signature -- vs. a single
   random outlier)
3. **What's likely causing it, and what should the operator do?** --
   tool wear, an incorrect offset, a loose fixture, or a placement/
   lighting issue, each with a concrete recommended action.

This is standard SPC practice on real production floors, and it's
honest about being a rules-based diagnostic engine -- which is also more
directly useful to an operator than an opaque confidence score.

## 5. Running it

### Start the hub
```bash
cd hub
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000`. You'll see an empty dashboard with an
"Add machine" prompt -- this is expected on first run; nothing is
pre-configured.

### See it working immediately, with no hardware
```bash
cd tools
pip install -r ../machine_node/requirements.txt
python simulate_floor.py --hub http://localhost:8000
```
This script uses the real API to add 3 demo machines, add and assign a
camera to each, upload a synthetic reference photo for each camera, then
streams synthetic "current part" photos continuously. One camera drifts
larger over time (watch the trend warning appear, then the eventual FAIL
with a tool-wear diagnosis); another throws occasional random defects
(flagged as isolated events, not a process issue). Best way to demo this
for a viva or a stakeholder before any real camera is wired up.

### Connect a real camera
Two options -- see `HARDWARE_AND_WIRING.md` for full parts lists and
wiring diagrams:

- **ESP32-CAM (fully wireless)** -- flash `firmware/esp32cam/esp32cam.ino`
  with your WiFi details, the hub's address, and the API key you got
  from Admin -> Cameras. No PC needed at the machine at all.
- **Laptop/mini-PC + USB webcam**:
  ```bash
  cd machine_node
  pip install -r requirements.txt
  python node_client.py --api-key CAM-XXXXXX --hub http://<hub-ip>:8000 --mode interval --interval 5
  ```

Either way: once the camera is live, go to that camera's card (or
Admin -> Cameras -> Reference) and capture its reference part photo --
inspections are scored from that point on.

## 6. Folder structure
See the repository README for the current layout.

## 7. Good "future scope" talking points for your report

- Currently measures one bounding box (width x height) per part; multiple
  contours per image could give multi-feature measurement (diameter +
  length + hole positions) in one photo.
- The diagnostics engine is deliberately rules-based/explainable; a
  trained CNN defect classifier could run alongside it for surface
  defects (scratches, cracks) that size comparison alone can't catch.
- Notifications currently cover on-page alerts + browser desktop
  notifications; email/SMS/webhook alerts (e.g. via a service like
  Twilio) would be a natural extension for an unattended line.
- No authentication yet -- fine for a lab/college or trusted factory LAN
  demo; add login + HTTPS before exposing this beyond a trusted network.

## XIAO ESP32S3 Sense wireless camera (new)
See `docs/XIAO_SETUP_GUIDE.md`. Dashboard camera cards now have **Capture photo** (camera sends a preview -> "Use as reference" / "Test"), and **Auto-test every N s**. Test without hardware: `python tools/simulate_xiao.py --api-key CAM-XXXXXX`.
