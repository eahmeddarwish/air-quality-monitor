/*
  Air Quality Monitor — Sensor Node
  =================================
  Reads 7 environmental metrics from a bench of discrete sensors and streams
  them over Serial as one JSON line per sample, at 115200 baud:

    {"temperature":24,"humidity":41,"uv":1.35,"h2s":2.10,"dust":0.08,"co2":420,"tvoc":5}

  This firmware only talks Serial — it has no network stack, no WiFi
  credentials, and no API keys. All cloud upload / AI analysis happens on
  the PC side (see ../../app/air_quality_dashboard.py), which is the only
  place any credentials are configured (via environment variables).

  HARDWARE
  --------
  - DHT11 (temperature + humidity)              -> digital pin 2
  - UV sensor (analog, e.g. ML8511-class)        -> analog pin A0
  - H2S gas sensor (analog)                      -> analog pin A2
  - Dust sensor (e.g. GP2Y1010AU0F / Sharp-style) -> analog pin A1, LED pin 7
  - Adafruit CCS811 (eCO2 + TVOC, I2C)            -> SDA/SCL

  See ../../README.md for the full wiring table and calibration notes.
*/

#include <DHT11.h>
#include <Wire.h>
#include "Adafruit_CCS811.h"

// ---------------- Pins ----------------
#define DHT_PIN     2   // DHT11 data
#define UV_PIN      A0  // UV sensor analog output
#define H2S_PIN     A2  // H2S sensor analog output
#define DUST_PIN    A1  // Dust sensor analog output
#define DUST_LED    7   // Dust sensor internal LED control

DHT11 dht(DHT_PIN);
Adafruit_CCS811 co2Sensor;

// ---------------- Calibration constants ----------------
// UV: converts the raw ADC reading to an approximate UV intensity (uW/cm^2).
// This factor is sensor-specific — recalibrate against a reference UV meter
// if you swap sensor units.
const float UV_CALIBRATION_FACTOR = 2.87;

// H2S: analog range is mapped linearly to 0..H2S_MAX_PPM. This is a coarse
// approximation, not a lab-grade calibration — see README "Honest limitations".
const float H2S_MAX_PPM = 50.0;
const int   ADC_MAX = 1023;

// Dust sensor timing, per the Sharp/GP2Y1010AU0F datasheet: pulse the LED on,
// sample partway through the pulse, then let the LED settle before the next
// read. These microsecond delays are short and sensor-mandated, not general
// program stalls.
const unsigned int DUST_SAMPLE_US = 280;
const unsigned int DUST_DELTA_US  = 40;
const unsigned int DUST_SLEEP_US  = 9680;

const unsigned long SAMPLE_INTERVAL_MS = 300;

void setup() {
  Serial.begin(115200);
  pinMode(DHT_PIN, INPUT);
  pinMode(DUST_LED, OUTPUT);

  Serial.println(F("Initializing CCS811..."));
  if (!co2Sensor.begin()) {
    Serial.println(F("CCS811 not found — check wiring."));
    while (1) { delay(1000); }
  }

  unsigned long start = millis();
  while (!co2Sensor.available()) {
    if (millis() - start > 30000UL) {
      Serial.println(F("CCS811 stabilization timeout."));
      while (1) { delay(1000); }
    }
    delay(1000);
  }

  Serial.println(F("Air Quality Monitor ready."));
}

void loop() {
  int temperature = dht.readTemperature();
  int humidity = dht.readHumidity();
  if (temperature == DHT11::ERROR_TIMEOUT) temperature = 0;
  if (humidity == DHT11::ERROR_TIMEOUT) humidity = 0;

  float uvIntensity = analogRead(UV_PIN) / UV_CALIBRATION_FACTOR;
  float h2sPpm = (analogRead(H2S_PIN) / (float)ADC_MAX) * H2S_MAX_PPM;

  digitalWrite(DUST_LED, LOW);          // sensor LED on
  delayMicroseconds(DUST_SAMPLE_US);
  int dustRaw = analogRead(DUST_PIN);
  delayMicroseconds(DUST_DELTA_US);
  digitalWrite(DUST_LED, HIGH);         // sensor LED off
  delayMicroseconds(DUST_SLEEP_US);
  float dustVoltage = dustRaw * (5.0 / 1024.0);
  float dustDensity = 0.17 * dustVoltage - 0.1;  // Sharp application-note formula
  if (dustDensity < 0) dustDensity = 0;

  uint16_t co2 = 0, tvoc = 0;
  if (co2Sensor.available() && !co2Sensor.readData()) {
    co2 = co2Sensor.geteCO2();
    tvoc = co2Sensor.getTVOC();
  }

  Serial.print(F("{\"temperature\":"));
  Serial.print(temperature);
  Serial.print(F(",\"humidity\":"));
  Serial.print(humidity);
  Serial.print(F(",\"uv\":"));
  Serial.print(uvIntensity, 2);
  Serial.print(F(",\"h2s\":"));
  Serial.print(h2sPpm, 2);
  Serial.print(F(",\"dust\":"));
  Serial.print(dustDensity, 2);
  Serial.print(F(",\"co2\":"));
  Serial.print(co2);
  Serial.print(F(",\"tvoc\":"));
  Serial.print(tvoc);
  Serial.println(F("}"));

  delay(SAMPLE_INTERVAL_MS);
}
