# Hardware & Electrical Setup — Wireless Inspection Cameras

This covers the physical camera side: what to buy, how to wire it, and
where to mount it at each machine's output. The hub software (running on
a PC/server) doesn't need any special hardware beyond a normal computer
on your factory network.

There are two ways to get a camera talking to the hub. Pick per-machine —
you can mix both across your floor.

---

## Option A — ESP32-CAM (recommended: fully wireless, ~Rs.500–800 per unit)

A postage-stamp-sized board with a camera and WiFi built in. No PC needed
at the machine at all — it talks straight to the hub over WiFi. Firmware
is provided in `firmware/esp32cam/esp32cam.ino`.

### Parts list (per camera)

| # | Item | Approx. price (₹) | Notes |
|---|------|------|-------|
| 1 | ESP32-CAM (AI-Thinker board, OV2640 sensor) | 450–650 | The camera module itself |
| 2 | FTDI / USB-to-serial programmer (3.3V/5V) | 120–200 | Only needed once, to flash the firmware — can be shared across all cameras |
| 3 | 5V / 1A (minimum) regulated power supply | 100–200 | A phone charger works; see power note below |
| 4 | 1000µF electrolytic capacitor | 5–15 | Across the 5V/GND supply pins — prevents brownout resets during WiFi transmission |
| 5 | Dupont jumper wires (F-F and M-F) | 30–50 | For FTDI-to-board programming connections |
| 6 | Small enclosure or 3D-printed mount | 100–300 | Weatherproofing/dust protection for a factory floor |
| 7 | LED ring light (optional, if not using the onboard flash) | 300–500 | Better, more even illumination than the onboard LED for consistent measurements |
| 8 | Momentary push button (optional) | 10–20 | Manual "inspect now" trigger, wired to GPIO13 + GND |

### Electrical connections

**Programming (one-time, per board, using the FTDI adapter):**

| ESP32-CAM pin | FTDI adapter pin |
|---|---|
| 5V | 5V |
| GND | GND |
| U0R (RX) | TX |
| U0T (TX) | RX |
| GPIO0 | GND (bridge this only while flashing — remove after) |

After wiring, plug the FTDI into your PC, select the correct COM port and
"AI Thinker ESP32-CAM" board in Arduino IDE, and upload
`esp32cam_client.ino` with your WiFi/hub details filled in. Then
disconnect the GPIO0-GND bridge and power-cycle the board — it now runs
standalone.

**Normal operation (no PC involved):**

| ESP32-CAM pin | Connects to |
|---|---|
| 5V | Positive rail of your 5V/1A supply |
| GND | Negative rail of your 5V supply |
| GPIO13 (optional) | One leg of the push button; other leg to GND |
| GPIO4 | Internal, drives the onboard flash LED automatically — no wiring needed |

**Power note:** the ESP32-CAM draws short current spikes (~300mA) while
transmitting over WiFi. A weak or long, thin USB cable causes voltage
sag and random resets ("brownout"). Use a proper 5V/1A+ supply as close
to the board as practical, and add the 1000µF capacitor across 5V/GND
right at the board if you see it restarting unexpectedly.

### Mounting

- Mount at the machine's **output side** (conveyor exit, ejection chute,
  or inspection tray) — wherever the finished part naturally comes to
  rest or passes through, per your original plan.
- Lens pointing straight down (perpendicular) at a fixed platform/tray —
  just like the RBL benchtop prototype, consistent geometry matters more
  than the camera being fancy.
- Keep the same distance and angle every time you re-capture a reference
  photo, or the reference measurement stops being comparable to later
  captures. If you ever move the camera, re-capture the reference.
- Add a plain, high-contrast background (a piece of matte card) behind/
  under the part in the camera's view, same as the earlier build guide —
  this is still the single biggest factor in reliable edge detection.
- Route the WiFi antenna (small onboard trace or external if your module
  has a u.FL connector) away from large metal machine bodies where
  possible — metal enclosures can weaken WiFi signal noticeably.

---

## Option B — Phone-as-camera or laptop webcam (fastest to get started, no soldering)

Good for a pilot/demo on one or two machines before committing to
ESP32-CAM hardware everywhere.

- **Laptop/mini-PC + USB webcam**, running `machine_node/node_client.py`.
  Wired or WiFi network connection to the hub, whichever the machine's
  location allows.
- **Old Android phone** running an IP-camera app (e.g. "IP Webcam") —
  mount the phone itself at the output side, and instead of
  `node_client.py` capturing from a local USB webcam, you can adapt it to
  pull the phone's MJPEG stream over WiFi (ask if you'd like this
  variant of the script — it's a small change).

This path has zero electrical wiring — just power (a phone charger or
laptop's own battery/adapter) and WiFi. It's the right choice for
proving the concept on one line before investing in ESP32-CAM units
across the whole floor.

---

## Network requirements (either option)

- All cameras and the hub PC must be on the **same local network**
  (same WiFi router/switch, or routed together) so the camera can reach
  the hub's IP address and port 8000.
- Give the hub PC a **static local IP** (or a DHCP reservation on your
  router) — if its IP changes, every camera's `HUB_URL` needs updating.
  Check your router's admin page for "DHCP reservation" or "static
  lease" to set this once.
- No internet connection is required — this all runs on your local
  factory network. If you do want remote access (checking the dashboard
  from off-site), that needs additional setup (port forwarding + a
  security review) which isn't included here.

---

## Quick reference: from unboxing to first inspection

1. Start the hub (`python -m uvicorn app:app --host 0.0.0.0 --port 8000`).
2. On the dashboard: **Admin -> Machines -> Add machine** (give it a
   name, location, and max error %).
3. **Admin -> Cameras -> Add camera**, assign it to that machine. Copy
   the generated API key (`CAM-XXXXXX`) shown when it's created.
4. Wire and flash an ESP32-CAM with that API key (or set up a webcam +
   `node_client.py --api-key CAM-XXXXXX`).
5. Power on the camera at the machine. Place a known-good part in view
   and click **"Set reference part"** on that camera's card (or run
   `node_client.py --api-key CAM-XXXXXX --hub <url> --set-reference --camera-id <numeric id>`).
6. From then on, every part that camera photographs is compared against
   its own reference automatically, with the % error and diagnosis shown
   live on the dashboard.
