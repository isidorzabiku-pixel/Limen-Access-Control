// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include "Arduino.h"
// Finto file system: un solo file /config.txt con contenuto impostabile dai test.
inline std::string& finto_config() { static std::string c; return c; }
inline bool& finto_presente() { static bool p = false; return p; }
struct File { bool ok = false; operator bool() const { return ok; } String readString() { String r; r.s = finto_config(); return r; } void close() {} size_t write(const uint8_t*, size_t n) { return n; } };
struct LittleFST {
  bool begin(bool = false) { return true; }
  bool exists(const String&) { return finto_presente(); }
  File open(const String&, const char*) { File f; f.ok = true; return f; }
  bool remove(const String&) { finto_presente() = false; return true; }
  bool rename(const String&, const String&) { return true; } bool format() { return true; }
} inline LittleFS;
