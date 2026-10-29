// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include "WiFi.h"
struct WiFiUDP {
  bool begin(uint16_t) { return true; } int parsePacket() { return 0; } IPAddress remoteIP() { return IPAddress(); } int read(uint8_t*, size_t) { return 0; } void flush() {}
  int beginPacket(IPAddress, uint16_t) { return 1; } size_t write(const uint8_t*, size_t n) { return n; } int endPacket() { return 1; }
};
