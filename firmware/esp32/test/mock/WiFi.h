// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include "Arduino.h"
enum { WL_CONNECTED = 3, WIFI_STA = 1, WIFI_AP_STA = 3 };
struct IPAddress {
  uint32_t v = 0;
  IPAddress() {}
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) : v((a << 24) | (b << 16) | (c << 8) | d) {}
  bool fromString(const String&) { return true; }
  String toString() const { return String("0.0.0.0"); }
};
struct WiFiT {
  void mode(int) {} int getMode() { return 1; }
  void setHostname(const char*) {} void config(IPAddress, IPAddress, IPAddress, IPAddress) {}
  void begin(const char*, const char*) {} int status() { return WL_CONNECTED; } void setSleep(bool) {} void disconnect() {}
  IPAddress localIP() { return IPAddress(); } IPAddress softAPIP() { return IPAddress(); } bool softAP(const char*, const char*) { return true; }
  String macAddress() { return String("AA:BB:CC:DD:EE:FF"); } int RSSI() { return -50; }
} inline WiFi;
