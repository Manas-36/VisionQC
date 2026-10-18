/*
 * VisionQC - camera firmware for Seeed Studio XIAO ESP32S3 Sense
 * ------------------------------------------------------------------------
 * What it does:
 *   1. Connects to your WiFi.
 *   2. Every ~2 seconds asks the hub: "any orders for me?"  (poll)
 *        - Admin pressed "Capture photo"  -> takes a photo, sends it as a PREVIEW
 *          (admin looks at it on the dashboard and can set it as the REFERENCE).
 *        - Admin switched on "Auto-test every N seconds" -> every N seconds takes a
 *          photo and sends it for INSPECTION (scored as % error vs the reference).
 *   3. The hub never has to reach the camera - the camera always calls the hub,
 *      so it works on any normal WiFi / phone hotspot with no router settings.
 *
 * Arduino IDE settings (Tools menu) - IMPORTANT:
 *   Board:  "XIAO_ESP32S3"      (install "esp32 by Espressif Systems" in Boards Manager)
 *   PSRAM:  "OPI PSRAM"         <-- camera will NOT work without this
 *   Upload Speed: 921600, USB CDC On Boot: "Enabled" (so Serial Monitor works)
 *
 * No extra libraries needed.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// WiFi / hub settings live in secrets.h (git-ignored so passwords never get committed).
// Copy secrets.h.example to secrets.h next to this file and fill it in.
#include "secrets.h"

#define POLL_INTERVAL_MS  2000
#define LED_PIN           21       // XIAO user LED (LOW = on)

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
int  autoIntervalSec = 10;
unsigned long lastPoll = 0;
unsigned long lastAuto = 0;

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
  config.frame_size   = FRAMESIZE_VGA;      // 640x480 - plenty for edge measuring. Try FRAMESIZE_SVGA for more detail.
  config.jpeg_quality = 12;                 // 10 (best) .. 63 (worst)
  config.fb_count     = 2;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST; // always give the newest frame, not an old buffered one

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x  (is PSRAM set to OPI PSRAM? is the camera ribbon seated?)\n", err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    s->set_whitebal(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_gain_ctrl(s, 1);
  }
  return true;
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400); Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nConnected. Camera IP: %s\n", WiFi.localIP().toString().c_str());
    blink(3);
  } else {
    Serial.println("\nWiFi failed - will retry.");
  }
}

// Take a photo (throw away one stale frame first so the picture is current).
camera_fb_t* takePhoto() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);
  return esp_camera_fb_get();
}

// Send a JPEG to the hub. url = full URL. If sendKeyField, API key goes in the form (for /api/inspect).
int postPhoto(const String& url, camera_fb_t* fb, bool sendKeyField) {
  const String boundary = "----XIAOBoundary7MA4YWxk";
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
  if (code != 200) Serial.println("Hub said: " + http.getString());
  http.end();
  free(body);
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

void sendInspection() {
  camera_fb_t* fb = takePhoto();
  if (!fb) { Serial.println("Capture failed"); return; }
  int code = postPhoto(String(HUB_URL) + "/api/inspect", fb, true);
  Serial.printf("Auto-test photo sent (%u bytes) -> HTTP %d\n", fb->len, code);
  esp_camera_fb_return(fb);
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
    int iv          = jsonValue(body, "auto_interval_sec").toInt();
    autoIntervalSec = iv >= 2 ? iv : 10;
    if (autoEnabled && !wasAuto) lastAuto = 0;   // start right away when switched on
    http.end();
    if (command == "capture") sendPreview();
  } else if (code == 404) {
    Serial.println("Hub: unknown API key - check API_KEY matches Admin -> Cameras.");
    http.end();
  } else {
    Serial.printf("Cannot reach hub (%d). Check HUB_URL and that the laptop is on the same WiFi.\n", code);
    http.end();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  Serial.println("\n=== XIAO ESP32S3 Sense - Vision Inspection camera ===");
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
  if (autoEnabled && assigned && hasReference &&
      (lastAuto == 0 || now - lastAuto >= (unsigned long)autoIntervalSec * 1000UL)) {
    lastAuto = now;
    sendInspection();
  }
  delay(20);
}
