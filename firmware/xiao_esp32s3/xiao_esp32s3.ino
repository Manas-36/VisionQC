/*
 * VisionQC - camera firmware for Seeed Studio XIAO ESP32S3 Sense
 * ------------------------------------------------------------------------
 * Modes (chosen on the dashboard, camera card -> Auto-test):
 *   - Capture photo button  -> camera sends a PREVIEW (used to set the reference)
 *   - Auto-test "every N s" -> timer mode, for bench / demo work
 *   - Auto-test "on sensor" -> a photo-eye / break-beam on pin D1 fires the shot
 *                              when a part is in position (the right way on a real line)
 *
 * The camera always calls the hub (poll every 2 s), so it works behind any WiFi.
 *
 * Arduino IDE settings (Tools menu):
 *   Board: "XIAO_ESP32S3"  (Boards Manager: "esp32 by Espressif Systems")
 *   PSRAM: "OPI PSRAM"     <-- camera will NOT start without it
 *   USB CDC On Boot: Enabled
 * Plug in the U.FL antenna that comes with the Sense - without it WiFi range is poor.
 *
 * SENSOR WIRING (sensor mode): sensor output -> D1 (GPIO2), sensor GND -> GND, sensor VCC -> 3V3 or 5V
 * per its datasheet. D1 is 3.3 V logic and has an internal pull-up: the sensor must pull the pin to
 * GND when a part is present (open-collector / NPN style). NEVER connect a 12/24 V sensor directly -
 * use an opto-isolator (see docs/HARDWARE_AND_WIRING.md).
 *
 * No extra libraries needed.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// WiFi / hub settings live in secrets.h (git-ignored so passwords never get committed).
// Copy secrets.h.example to secrets.h next to this file and fill it in.
#include "secrets.h"

// ---------------------------- tunables ----------------------------------
#define POLL_INTERVAL_MS     2000
#define FRAME_SIZE           FRAMESIZE_SXGA   // 1280x1024. Measurement accuracy scales with pixels.
                                              // FRAMESIZE_VGA=640x480 (fast), FRAMESIZE_UXGA=1600x1200 (best)
#define JPEG_QUALITY         10               // 10 = best .. 63 = worst. Keep low: compression noise blurs edges
#define LOCK_EXPOSURE        0                // 1 = fixed exposure/gain/white-balance (recommended with a constant light)
#define EXPOSURE_VALUE       300              // 0..1200, only used if LOCK_EXPOSURE = 1
#define SENSOR_PIN           D1               // photo-eye input, active LOW
#define SENSOR_DEBOUNCE_MS   40               // ignore contact chatter
#define SENSOR_SETTLE_MS     150              // wait after the beam breaks so the part has stopped moving
#define SENSOR_LOCKOUT_MS    500              // minimum time between two shots (one part = one photo)
#define UPLOAD_RETRIES       3
#define WIFI_RESTART_AFTER_MS 90000           // reboot if WiFi has been down this long
#define LED_PIN              21               // XIAO user LED (LOW = on)

// ---- XIAO ESP32S3 Sense camera pins (OV2640 / OV3660 on the Sense board) ----
#define PWDN_GPIO_NUM   -1
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM   10
#define SIOD_GPIO_NUM   40
#define SIOC_GPIO_NUM   39
#define Y9_GPIO_NUM     48
#define Y8_GPIO_NUM     11
#define Y7_GPIO_NUM     12
#define Y6_GPIO_NUM     14
#define Y5_GPIO_NUM     16
#define Y4_GPIO_NUM     18
#define Y3_GPIO_NUM     17
#define Y2_GPIO_NUM     15
#define VSYNC_GPIO_NUM  38
#define HREF_GPIO_NUM   47
#define PCLK_GPIO_NUM   13

bool autoEnabled = false;
bool hasReference = false;
bool assigned = false;
bool sensorMode = false;
int  autoIntervalSec = 10;
unsigned long lastPoll = 0;
unsigned long lastAuto = 0;
unsigned long wifiDownSince = 0;

volatile bool sensorFlag = false;
volatile unsigned long sensorFlagAt = 0;
unsigned long lastShotAt = 0;

void IRAM_ATTR onSensor() {
  unsigned long now = millis();
  if (!sensorFlag && now - sensorFlagAt > SENSOR_DEBOUNCE_MS) {
    sensorFlag = true;
    sensorFlagAt = now;
  }
}

void blink(int n) {
  for (int i = 0; i < n; i++) {
    digitalWrite(LED_PIN, LOW); delay(60);
    digitalWrite(LED_PIN, HIGH); delay(60);
  }
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAME_SIZE;
  config.jpeg_quality = JPEG_QUALITY;
  config.fb_count     = 2;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST;   // always the newest frame, never a stale buffered one

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x  (PSRAM set to OPI PSRAM? camera board clicked in fully?)\n", err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
#if LOCK_EXPOSURE
    s->set_exposure_ctrl(s, 0);  s->set_aec_value(s, EXPOSURE_VALUE);
    s->set_gain_ctrl(s, 0);      s->set_agc_gain(s, 0);
    s->set_whitebal(s, 0);       s->set_awb_gain(s, 0);
#else
    s->set_exposure_ctrl(s, 1);  s->set_gain_ctrl(s, 1);  s->set_whitebal(s, 1);
#endif
    s->set_lenc(s, 1);           // lens shading correction (dark corners)
  }
  // Let auto-exposure settle: first frames after power-up are badly exposed.
  for (int i = 0; i < 5; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
    delay(100);
  }
  return true;
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) { wifiDownSince = 0; return; }
  if (wifiDownSince == 0) wifiDownSince = millis();
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);               // no modem power-save: lower, steadier latency
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400); Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nConnected. Camera IP: %s  RSSI: %d dBm (better than -70 is OK)\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    wifiDownSince = 0;
    blink(3);
  } else {
    Serial.println("\nWiFi failed - will retry.");
    if (millis() - wifiDownSince > WIFI_RESTART_AFTER_MS) ESP.restart();   // self-heal
  }
}

// Take a photo (throw away one buffered frame first so the picture is current).
camera_fb_t* takePhoto() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);
  return esp_camera_fb_get();
}

// Send a JPEG to the hub. If sendKeyField, the API key goes in the form (for /api/inspect).
int postPhotoOnce(const String& url, camera_fb_t* fb, bool sendKeyField) {
  const String boundary = "----VisionQCBoundary7MA4YWxk";
  String head = "";
  if (sendKeyField) {
    head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"api_key\"\r\n\r\n" + String(API_KEY) + "\r\n";
  }
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"photo.jpg\"\r\n"
          "Content-Type: image/jpeg\r\n\r\n";
  String tail = "\r\n--" + boundary + "--\r\n";

  size_t total = head.length() + fb->len + tail.length();
  uint8_t* body = (uint8_t*)ps_malloc(total);
  if (!body) { Serial.println("Out of memory for upload"); return -1; }
  memcpy(body, head.c_str(), head.length());
  memcpy(body + head.length(), fb->buf, fb->len);
  memcpy(body + head.length() + fb->len, tail.c_str(), tail.length());

  HTTPClient http;
  http.setTimeout(15000);
  http.begin(url);
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
  int code = http.POST(body, total);
  if (code != 200 && code > 0) Serial.println("Hub said: " + http.getString());
  http.end();
  free(body);
  return code;
}

// Retry on network trouble (code <= 0) or hub 5xx; a 4xx means the request itself is wrong, don't retry.
int postPhoto(const String& url, camera_fb_t* fb, bool sendKeyField) {
  int code = -1;
  for (int attempt = 1; attempt <= UPLOAD_RETRIES; attempt++) {
    code = postPhotoOnce(url, fb, sendKeyField);
    if (code == 200 || (code >= 400 && code < 500)) break;
    Serial.printf("Upload attempt %d failed (%d)\n", attempt, code);
    delay(300 * attempt);
    if (WiFi.status() != WL_CONNECTED) break;
  }
  return code;
}

// Tiny JSON helpers (avoids needing a JSON library)
String jsonValue(const String& json, const String& key) {
  int k = json.indexOf("\"" + key + "\"");
  if (k < 0) return "";
  int c = json.indexOf(':', k);
  int e = json.indexOf(',', c);
  int e2 = json.indexOf('}', c);
  if (e < 0 || (e2 >= 0 && e2 < e)) e = e2;
  String v = json.substring(c + 1, e);
  v.trim();
  v.replace("\"", "");
  return v;
}

void sendPreview() {
  Serial.println("Admin asked for a photo -> capturing preview...");
  blink(1);
  camera_fb_t* fb = takePhoto();
  if (!fb) { Serial.println("Capture failed"); return; }
  int code = postPhoto(String(HUB_URL) + "/api/device/" + API_KEY + "/photo", fb, false);
  Serial.printf("Preview sent (%u bytes) -> HTTP %d\n", fb->len, code);
  esp_camera_fb_return(fb);
}

void sendInspection(const char* why) {
  camera_fb_t* fb = takePhoto();
  if (!fb) { Serial.println("Capture failed"); return; }
  int code = postPhoto(String(HUB_URL) + "/api/inspect", fb, true);
  Serial.printf("[%s] photo sent (%u bytes) -> HTTP %d\n", why, fb->len, code);
  esp_camera_fb_return(fb);
  lastShotAt = millis();
}

void pollHub() {
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(String(HUB_URL) + "/api/device/" + API_KEY + "/poll");
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    String command = jsonValue(body, "command");
    assigned        = jsonValue(body, "assigned") == "true";
    hasReference    = jsonValue(body, "has_reference") == "true";
    bool wasAuto    = autoEnabled;
    autoEnabled     = jsonValue(body, "auto_enabled") == "true";
    sensorMode      = jsonValue(body, "mode") == "sensor";
    int iv          = jsonValue(body, "auto_interval_sec").toInt();
    autoIntervalSec = iv >= 2 ? iv : 10;
    if (autoEnabled && !wasAuto) lastAuto = 0;   // start right away when switched on
    http.end();
    if (command == "capture") sendPreview();
  } else if (code == 404) {
    Serial.println("Hub: unknown API key - check API_KEY matches Admin -> Cameras.");
    http.end();
  } else {
    Serial.printf("Cannot reach hub (%d). Check HUB_URL (dashboard -> Admin -> Cameras shows it) and WiFi.\n", code);
    http.end();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), onSensor, FALLING);
  Serial.println("\n=== VisionQC XIAO ESP32S3 Sense camera ===");
  if (!initCamera()) { while (true) { blink(5); delay(1000); } }
  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) { connectWiFi(); delay(1000); return; }

  unsigned long now = millis();
  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;
    pollHub();
  }

  bool ready = autoEnabled && assigned && hasReference;

  if (ready && sensorMode) {
    // Photo-eye triggered: one shot per part.
    if (sensorFlag) {
      delay(SENSOR_SETTLE_MS);                       // part must stop moving before we shoot
      sensorFlag = false;
      if (millis() - lastShotAt >= SENSOR_LOCKOUT_MS) sendInspection("sensor");
    }
  } else {
    sensorFlag = false;
  }

  if (ready && !sensorMode &&
      (lastAuto == 0 || now - lastAuto >= (unsigned long)autoIntervalSec * 1000UL)) {
    lastAuto = now;
    sendInspection("timer");
  }
  delay(5);
}
