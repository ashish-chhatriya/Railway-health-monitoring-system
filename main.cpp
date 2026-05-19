#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <Firebase_ESP_Client.h>
#include <DHT.h>
// These come from inside the Firebase folder you just pasted
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>

// 1. YOUR DETAILS
#define API_KEY "BEH0gtgnxPym_6Ux-jAQNFsfL4dHD4-QQ9G3EgTqsFWKatuyr6pWL4Iq5GyAGB-oweOnVszwzWE7WKSd8s10dx4"
#define DATABASE_URL "https://railway-monitoring-system-default-rtdb.asia-southeast1.firebasedatabase.app"
#define WIFI_SSID "Wokwi-GUEST" 
#define WIFI_PASSWORD ""

// 2. PIN DEFS
#define DHTPIN D2   // Pin D2 on NodeMCU
#define DHTTYPE DHT22
#define VIB_PIN A0  // The only Analog pin on ESP8266
#define TRIG_PIN D5
#define ECHO_PIN D6

const char *RFID_TAGS[] = {
  "Track1-1", "Track1-2", "Track1-3", "Track1-4", "Track1-5",
  "Track1-6", "Track1-7", "Track1-8", "Track1-9", "Track1-10"
};
const int RFID_TAG_COUNT = sizeof(RFID_TAGS) / sizeof(RFID_TAGS[0]);

DHT dht(DHTPIN, DHTTYPE);
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

unsigned long sendDataPrevMillis = 0;
int rfidIndex = 0;

float readDistanceMm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return 143.5;
  }

  // Convert ultrasonic echo duration (microseconds) into millimeters.
  return (duration * 0.343f) / 2.0f;
}

void setup() {
  Serial.begin(115200);
  dht.begin();
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected!");

  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  Firebase.signUp(&config, &auth, "", "");
  config.token_status_callback = tokenStatusCallback; 
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
}

void loop() {
  if (Firebase.ready() && (millis() - sendDataPrevMillis > 5000)) {
    sendDataPrevMillis = millis();

    int vib = analogRead(VIB_PIN); // Reads 0-1023
    float t = dht.readTemperature();
    float distanceMm = readDistanceMm();
    const char *rfidBefore = RFID_TAGS[rfidIndex];
    const char *rfidAfter = RFID_TAGS[(rfidIndex + 1) % RFID_TAG_COUNT];
    rfidIndex = (rfidIndex + 1) % RFID_TAG_COUNT;

    FirebaseJson json;
    json.set("vibration", vib);
    json.set("temp", t);
    json.set("distance", distanceMm);
    json.set("rfid_before", rfidBefore);
    json.set("rfid_after", rfidAfter);
    json.set("ts", millis());
    json.set("status", (vib > 700) ? "CRITICAL" : "Stable");

    Firebase.RTDB.setJSON(&fbdo, "/track_monitor/live", &json);
    Serial.printf("Vib: %d | Temp: %.1f | Distance: %.2f mm | %s -> %s\n", vib, t, distanceMm, rfidBefore, rfidAfter);
  }
}
