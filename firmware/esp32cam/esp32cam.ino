/*
  ESP32-CAM wireless inspection camera
  =====================================
  Board: AI-Thinker ESP32-CAM (the common, cheap ~Rs.500-700 module)

  What this does:
    - Connects to your factory WiFi
    - Every CAPTURE_INTERVAL_MS, takes a photo and POSTs it (multipart/
      form-data) to the hub's /api/inspect endpoint, exactly like
      node_client.py does from a laptop — except this needs no PC at all.
    - Optionally, holding the trigger button (GPIO13, wired to GND)
      forces an immediate capture instead of waiting for the timer —
      useful if you'd rather trigger on a part-present sensor later.
    - Blinks the onboard flash LED briefly before each capture (helps in
      dim machine enclosures; comment out USE_FLASH if you have your own
      ring light and don't want it).

  Before uploading, fill in:
    WIFI_SSID, WIFI_PASSWORD, HUB_URL, API_KEY

  Get API_KEY from the dashboard: Admin -> Cameras -> Add camera.
  Each physical ESP32-CAM should get its own api_key and its own copy
  of this sketch (just change API_KEY before flashing each one).

  Arduino IDE setup (one-time):
    1. File -> Preferences -> Additional Board Manager URLs, add:
       https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
    2. Tools -> Board -> Boards Manager -> install "esp32" (Espressif Systems)
    3. Tools -> Board -> select "AI Thinker ESP32-CAM"
    4. Tools -> Partition Scheme -> "Huge APP (3MB No OTA/1MB SPIFFS)"
    5. To upload: connect an FTDI/USB-serial adapter (GND-GND, TX-RX,
       RX-TX, 5V-5V), bridge GPIO0 to GND, press reset, then Upload.
       Remove the GPIO0-GND bridge and reset again to run normally.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"

// ---------------- EDIT THESE ----------------
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* HUB_URL       = "http://192.168.1.50:8000";   // your hub's address on the factory LAN
const char* API_KEY     = "CAM-XXXXXX";                  // from Admin -> Cameras on the dashboard
// ---------------------------------------------

#define CAPTURE_INTERVAL_MS 5000   // auto-capture every 5 seconds; tune to your line speed
#define USE_FLASH true
#define TRIGGER_PIN 13             // optional physical button: this pin to GND = capture now

// AI-Thinker ESP32-CAM pin map (do not change unless using a different board)
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22
#define FLASH_GPIO_NUM     4

unsigned long lastCapture = 0;

void setup() {
  Serial.begin(115200);
  pinMode(TRIGGER_PIN, INPUT_PULLUP);
  if (USE_FLASH) pinMode(FLASH_GPIO_NUM, OUTPUT);

  connectWiFi();
  initCamera();

  Serial.println("Ready. Capturing every " + String(CAPTURE_INTERVAL_MS) + " ms, or on button press.");
}

void loop() {
  bool buttonPressed = (digitalRead(TRIGGER_PIN) == LOW);
  bool timerDue = (millis() - lastCapture >= CAPTURE_INTERVAL_MS);

  if (buttonPressed || timerDue) {
    lastCapture = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi dropped, reconnecting...");
      connectWiFi();
    }
    captureAndSend();
    if (buttonPressed) delay(400);  // simple debounce
  }
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500); Serial.print("."); attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nConnected. IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWiFi connection failed — will retry in the loop.");
  }
}

void initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM; config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_SVGA;   // 800x600 — good balance of detail vs upload size
    config.jpeg_quality = 10;             // lower number = higher quality
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x — check wiring/power.\n", err);
  }
}

void captureAndSend() {
  if (USE_FLASH) { digitalWrite(FLASH_GPIO_NUM, HIGH); delay(80); }
  camera_fb_t* fb = esp_camera_fb_get();
  if (USE_FLASH) digitalWrite(FLASH_GPIO_NUM, LOW);

  if (!fb) {
    Serial.println("Capture failed.");
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("No WiFi — dropping this frame (ESP32-CAM has no local disk queue).");
    esp_camera_fb_return(fb);
    return;
  }

  HTTPClient http;
  http.begin(String(HUB_URL) + "/api/inspect");

  String boundary = "----esp32cam";
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);

  String head = "--" + boundary + "\r\n"
                "Content-Disposition: form-data; name=\"api_key\"\r\n\r\n" + String(API_KEY) + "\r\n"
                "--" + boundary + "\r\n"
                "Content-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n"
                "Content-Type: image/jpeg\r\n\r\n";
  String tail = "\r\n--" + boundary + "--\r\n";

  size_t totalLen = head.length() + fb->len + tail.length();
  uint8_t* body = (uint8_t*)malloc(totalLen);
  if (!body) {
    Serial.println("Out of memory building request body.");
    esp_camera_fb_return(fb);
    http.end();
    return;
  }
  memcpy(body, head.c_str(), head.length());
  memcpy(body + head.length(), fb->buf, fb->len);
  memcpy(body + head.length() + fb->len, tail.c_str(), tail.length());

  int httpCode = http.POST(body, totalLen);
  if (httpCode == 200) {
    Serial.println("Sent OK: " + http.getString());
  } else {
    Serial.printf("Hub responded with code %d: %s\n", httpCode, http.getString().c_str());
  }

  free(body);
  http.end();
  esp_camera_fb_return(fb);
}
