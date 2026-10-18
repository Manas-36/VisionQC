# Design review: what was wrong with VisionQC v1, and what changed

An honest review of the first version, in order of how badly each problem would hurt on a real line.
"Fixed" = changed in this release and covered by a test where possible. "Open" = still true, so state it in the report.

## Verdict
v1 was a convincing **demo** (it worked on synthetic rectangles), but not yet a **measurement system**. It
measured pixels, not millimetres; it could not tell "part changed" from "camera moved"; it fired photos on a
timer with no idea whether a part was present; it had no lighting design; and anyone on the network could
administer it. The statistics also had a flaw that would have produced false "drift" alarms from pure noise.

## Software

| # | Problem | Severity | Status |
|---|---------|----------|--------|
| S1 | **No millimetres.** Only % vs a reference photo. A "dimension inspection system" that cannot say "112.4 mm". | High | **Fixed**: optional real size entered with the reference -> mm per reading |
| S2 | **Fixed Canny thresholds + largest edge contour.** Sensitive to lighting; a shadow, belt edge or noise became "the part"; +2 px dilation bias. | High | **Fixed**: Otsu threshold, automatic polarity, morphology, ignores blobs touching the frame edge |
| S3 | **Empty frame = FAIL/ERROR alarm.** A timer between parts raised alerts and polluted pass-rate. | High | **Fixed**: `NO_PART` is ignored (not stored, not alerted) |
| S4 | **No image-quality gates.** Blurry / dark / cut-off images were measured anyway. | High | **Fixed**: focus, exposure and cut-off checks -> clear camera-problem message |
| S5 | **Drift rule counted noise.** 7 readings of +0.02 % (all "positive") = "systematic drift". | High | **Fixed**: noise dead-band (25 % of tolerance) |
| S6 | **Camera movement looked like a process fault** (tool wear alert when someone bumped the camera). | High | **Fixed**: length+width moving together -> "check the camera first" |
| S7 | **No statistics an engineer would accept.** No spread, no Cpk, no gauge-repeatability check. | Med | **Fixed**: Cpk badge, `/api/cameras/{id}/capability`, 6-sigma-vs-band rule |
| S8 | **No authentication.** Anyone on the LAN could add/delete machines, change references, read images. | High | **Fixed**: `VISIONQC_ADMIN_PASSWORD` (HTTP Basic) on dashboard, API, images, WebSocket. Cameras use their key. **Open**: plain HTTP, no per-user accounts, no audit log |
| S9 | **Guessable camera keys** (24-bit) on unauthenticated device endpoints. | Med | **Fixed**: 48-bit keys |
| S10 | **Stored XSS**: a machine named `<img onerror=...>` ran script in every viewer's browser. | High | **Fixed**: all dynamic text escaped; verified in a real browser |
| S11 | **Image endpoint path traversal** risk (backslashes on Windows). | Med | **Fixed** + test |
| S12 | **Unbounded uploads; OpenCV ran on the event loop** (one big photo froze the dashboard). | Med | **Fixed**: 6 MB cap, measurement in a thread pool |
| S13 | **Images stored forever** - disk fills; no retention. | Med | **Fixed**: PASS images 30 d, FAIL 180 d (configurable); measurements kept |
| S14 | **Camera dying was silent.** Parts then pass uninspected - the worst failure mode. | High | **Fixed**: instant "camera OFFLINE" alert (toast + beep) |
| S15 | **Hub IP changes** broke every camera (it happened during testing). | Med | **Mitigated**: dashboard shows the exact `HUB_URL`; docs require DHCP reservation. **Open**: no auto-discovery |
| S16 | SQLite per-call connections without WAL / busy timeout. | Low | **Fixed** |
| S17 | Single bounding box only; no hole positions, no surface defects; rules-based "AI". | Med | **Open** (scope): say so; CNN defect classifier = future work |
| S18 | No calibration for lens distortion / perspective. | Med | **Open**: mitigated by fixed part placement; documented |
| S19 | Reference = one golden sample; its own error becomes the "nominal". | Med | **Mitigated**: use a calibrated sample and enter its mm size. **Open**: no multi-sample reference |
| S20 | No reject/stop output - the system only reports. | Med | **Open**: future work (relay/diverter) |

## Hardware

| # | Problem | Severity | Status |
|---|---------|----------|--------|
| H1 | **No lighting design.** Ambient light makes edges wander; this alone defeats any tolerance. | Critical | **Fixed in design**: diffuse DC backlight + hood; optional exposure lock |
| H2 | **Free-running timer on a conveyor** photographs empty belt / half parts. | Critical | **Fixed**: `on sensor` mode - photo-eye on D1, settle delay, debounce, one shot per part |
| H3 | **Rolling-shutter blur** from a moving part not addressed. | High | **Documented**: stop-and-shoot / slow belt / strobed backlight; upgrade path to global-shutter |
| H4 | **VGA (640x480)** gave ~0.16 mm/px at a 100 mm field - useless for tight tolerances. | High | **Fixed**: default SXGA, UXGA selectable; resolution budget in the guide |
| H5 | **Overstated accuracy.** | High | **Fixed**: guide states realistic limits (about +/-0.3 mm usable) and how to verify them (Cpk) |
| H6 | **Mounting not rigid / not repeatable**; focus ring unlocked. | High | **Fixed in design**: aluminium profile, fixture guides, lens lock, re-take reference after any bump |
| H7 | **Power**: USB charger/cheap wire; no protection; 24 V plant power not addressed. | Med | **Fixed in design**: 24 V -> 5 V buck, fuse, TVS, bulk capacitor |
| H8 | **No safe sensor interface** (24 V sensor into 3.3 V GPIO would destroy the board). | High | **Fixed in design**: opto-isolator wiring |
| H9 | **WiFi reliance**: phone hotspot, no antenna note, no recovery. | Med | **Fixed**: dedicated 2.4 GHz AP, fit the U.FL antenna, retries, auto-reboot after 90 s without WiFi, RSSI print |
| H10 | **First frames after boot badly exposed.** | Low | **Fixed**: warm-up frames discarded |
| H11 | No enclosure / dust / coolant protection. | Med | **Fixed in design**: IP54 box with window |
| H12 | OV2640 itself: rolling shutter, barrel distortion, JPEG artefacts. | Med | **Open** (hardware limit) - hence the upgrade path |

## Not verified here
- The firmware was **not compiled or run on a physical XIAO** in this environment. The hub side (every endpoint
  the firmware calls) is covered by automated tests and a simulated device. Compile in Arduino IDE and report any error.
- The accuracy numbers are engineering estimates. Measure yours with the repeatability procedure (guide section 6).
