# Hardware & Electrical Guide (v2)

This replaces the first version, which was too optimistic. Read **section 1 first**: a vision gauge is
mostly a *lighting and mounting* problem, and only secondly a camera/software problem.

## 1. Ground rules (what actually decides accuracy)

1. **Light it from behind (backlight).** For a width/length measurement, a diffuse LED panel under or
   behind the part turns it into a crisp black silhouette. Ambient or front lighting gives shadows and
   reflections that move the detected edge by several pixels. A backlight is the single biggest upgrade.
2. **Constant light.** Use a DC-driven panel (no visible PWM flicker) and a shroud/hood that blocks
   room light and sunlight. Optionally lock exposure in firmware (`LOCK_EXPOSURE 1`).
3. **Rigid, repeatable geometry.** Camera and part position must be identical every time: aluminium
   profile mount, part guides/fixture so the part always sits in the same spot, camera perpendicular
   to the stage. The software compares against a reference taken from *this exact camera position*; if the
   camera is bumped, every reading shifts (the hub now warns "check the camera" when length and width
   change together).
4. **Stationary part when the shutter fires.** The OV2640 is a rolling-shutter sensor: a moving part is
   blurred and skewed. Use *stop-and-shoot*, a slow belt (< ~0.1 m/s) with the photo-eye trigger
   **and** a short exposure, or a strobed backlight. A free-running timer on a moving conveyor
   photographs half-parts and empty belt - use the sensor trigger on a real line, timer only on the bench.
5. **Know your resolution.** mm/pixel = field-of-view width / image width. Example: 100 mm field on
   SXGA (1280 px) -> 0.078 mm/px. Edge position is realistically good to ~0.3-0.5 px, so repeatability
   is roughly +/-0.03-0.05 mm and a *usable tolerance is about +/-0.3 mm or looser*. Do **not** claim
   +/-0.05 mm with this hardware. The dashboard's **Cpk badge / `/api/cameras/{id}/capability`** measures
   this for your actual set-up: leave one part untouched, run 30 shots, and check that 6 sigma is below
   ~30 % of the tolerance band.
6. **Lens distortion and perspective.** The stock OV2640 lens has barrel distortion and parts with
   height look bigger when closer to the lens. Always place the part in the same, central spot; keep the
   camera as far away as resolution allows; measure parts of the same height.

## 2. Parts list (per inspection station)

Prices are rough Indian-market estimates; check current listings.

| # | Item | Approx. Rs | Why |
|---|------|-----------|-----|
| 1 | Seeed XIAO ESP32S3 **Sense** (camera board attached) | 2,200-3,000 | Camera + WiFi, 8 MB PSRAM. USB-C, no FTDI needed |
| 2 | U.FL / IPEX antenna (ships with the board) | incl. | **Must be fitted** - on-board range is poor, metal machines make it worse |
| 3 | LED **backlight panel**, diffuse, ~A5, 12 V | 400-900 | Silhouette lighting (section 1) |
| 4 | 12 V 1-2 A regulated DC supply for the panel | 200-350 | Low-ripple, no PWM dimmer |
| 5 | **Photo-eye / break-beam sensor** (3.3-5 V IR module for bench; NPN/PNP industrial photoelectric for a line) | 150-1,500 | Triggers one photo per part |
| 6 | PC817 (or similar) opto-isolator + 2.2 k and 10 k resistors | 20-40 | Isolate any 12/24 V sensor from the 3.3 V GPIO |
| 7 | 24 V DIN PSU (if the line is 24 V) + 24 V -> 5 V / 2 A DC-DC converter | 800-1,500 | Camera supply from plant power |
| 8 | 1 A fuse + holder, SMBJ33A TVS diode (24 V rail) | 50-100 | Protect against shorts/transients |
| 9 | 20x20 aluminium profile + brackets (or sturdy 3D-printed mount) | 300-800 | Rigid, repeatable geometry |
| 10 | Black hood / shroud (matte black card, ABS, or 3D-print) | 100-300 | Block ambient light |
| 11 | Thread-lock / lens-lock ring | 20 | The focus ring must not drift |
| 12 | Enclosure (IP54 box with window) | 300-600 | Dust / coolant splash |
| 13 | Known-size reference part or gauge block / calliper-measured sample | - | Calibrates mm/pixel and gives the "golden sample" |
| 14 | Dedicated 2.4 GHz WiFi router / AP (not a phone) | 1,200-2,500 | See section 5 |

Bench/demo version: items 1, 2, a cheap backlight, a 5 V IR break-beam module, USB power bank. ~Rs 3,500.

## 3. Wiring

### 3.1 Camera power
```
 24 V DIN PSU --[1 A fuse]--+--[SMBJ33A TVS to GND]
                            |
                      24V->5V buck (>= 2 A) --> XIAO 5V pin (or USB-C)
                                          GND --> XIAO GND
```
The XIAO + camera + WiFi can pull ~400 mA peaks; use a >= 1 A supply with short, thick wires. A thin
long USB cable causes brown-out resets (visible as the board rebooting when it transmits). Add 470-1000 uF
across 5V/GND at the board.

### 3.2 Trigger sensor -> D1 (GPIO2)
D1 is **3.3 V logic with an internal pull-up**; the firmware fires when D1 goes **LOW**. Never connect 12/24 V.

**a) 3.3/5 V IR break-beam module (bench):**
```
 module VCC -> 3V3     module GND -> GND     module OUT -> D1
```
Use a module whose OUT is open-collector / goes LOW when the beam is blocked.

**b) 24 V industrial sensor through an opto-isolator (line):**
```
 24 V PNP sensor OUT --[2.2 k]--> PC817 pin1 (anode)       PC817 pin2 (cathode) --> 24 V GND
 PC817 pin4 (collector) --> XIAO D1          PC817 pin3 (emitter) --> XIAO GND
```
Sensor active -> opto conducts -> D1 pulled LOW -> photo. (For an NPN sensor feed the LED from +24 V
through 2.2 k and let the sensor sink the cathode.) The two grounds stay separate - that is the point.

### 3.3 Backlight
12 V supply -> panel, on its own supply. Do not power it from the 5 V camera rail.

### 3.4 Programming
USB-C cable straight to the laptop. If upload fails: hold **B**, tap **R**, release B, upload again.

## 4. Mechanical layout
```
        [XIAO camera]  <- rigid arm, looking straight down, lens locked
             |
        (hood / shroud)
             |
   ---- part on stage, in a guide/fixture ----
        [ diffuse LED backlight panel ]
   photo-eye across the stage, just before the part's stopping position
```
Mount at the machine **output**; the part should stop (or be nearly still) in the frame for ~150 ms.

## 5. Network & operations
- Use a **dedicated 2.4 GHz router/AP** on channel 1, 6 or 11, WPA2. The ESP32 cannot use 5 GHz. A phone
  hotspot is fine for a demo only - it changes IPs and sleeps.
- Give the hub PC a **DHCP reservation (fixed IP)** and use that as `HUB_URL`. Hub on Ethernet, on a UPS.
- Run the hub as a service (`deploy/visionqc.service` on Linux; NSSM or Task Scheduler on Windows) so a
  reboot does not silently stop inspection. The dashboard now alerts when a camera goes offline.
- Set `VISIONQC_ADMIN_PASSWORD`. Keep the network closed: camera traffic is plain HTTP. Put the hub behind
  an HTTPS reverse proxy (e.g. Caddy) if it must cross an untrusted network.
- Signal check: Serial Monitor prints RSSI at connect; better than -70 dBm is acceptable.

## 6. Commissioning checklist
1. Fit antenna; fix camera mount; set focus on the part plane; lock the lens.
2. Backlight on, hood on. The silhouette should be solid black, edges sharp.
3. Place a known part, **Capture photo** -> check preview -> enter its real length/width in mm -> **Use as reference**.
4. Repeatability: leave the part, set auto-test "every 2 s" for ~1 min. Read **Cpk** / 6-sigma. Only enforce
   tolerances your gauge can resolve (6 sigma <= ~30 % of the +/-tolerance band).
5. Switch to **on sensor**, run 20 parts, confirm one photo per part and no empty-frame alerts.
6. Re-do step 3 after *any* bump of the camera, lens or lighting.

## 7. Limits you should state in the report
- Two dimensions of one silhouette (length x width); no height, holes, threads or surface defects.
- Not calibrated for lens distortion; accuracy only valid for parts placed where the reference was taken.
- Inspection is advisory: the system alerts, it does not reject or stop the machine. Do not use it as a
  safety device. A reject actuator (diverter/relay) is future work.
- For tighter tolerances or fast conveyors, move to a global-shutter industrial camera with telecentric lens
  and strobed backlight (e.g. Raspberry Pi + Global Shutter camera as the next step).

## 8. Legacy: AI-Thinker ESP32-CAM
Still supported by `firmware/esp32cam/esp32cam.ino` (timer upload only, no remote capture/sensor mode). It
needs an FTDI adapter (GPIO0->GND while flashing), a clean 5 V/1 A supply, and its antenna is weaker. Prefer the XIAO.
