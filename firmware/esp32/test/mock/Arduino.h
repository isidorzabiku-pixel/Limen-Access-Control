// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Finti header Arduino/ESP32 per verificare che src/main.cpp compili (sintassi e tipi) senza la toolchain vera.
#pragma once
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#define PROGMEM
#define HIGH 1
#define LOW 0
#define INPUT_PULLUP 2
#define OUTPUT 1
class __FlashStringHelper;
#define FPSTR(p) (reinterpret_cast<const __FlashStringHelper*>(p))
class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const __FlashStringHelper* f) : s(reinterpret_cast<const char*>(f)) {}
  String(int v) : s(std::to_string(v)) {}
  String(unsigned v) : s(std::to_string(v)) {}
  String(long v) : s(std::to_string(v)) {}
  String(unsigned long v) : s(std::to_string(v)) {}
  String(char c) : s(1, c) {}
  size_t length() const { return s.size(); }
  const char* c_str() const { return s.c_str(); }
  char operator[](size_t i) const { return s[i]; }
  String& operator+=(const String& o) { s += o.s; return *this; }
  String& operator+=(const char* o) { s += o; return *this; }
  String& operator+=(char o) { s += o; return *this; }
  void reserve(size_t n) { s.reserve(n); }
  void trim() { while (!s.empty() && isspace((unsigned char)s.back())) s.pop_back(); size_t i = 0; while (i < s.size() && isspace((unsigned char)s[i])) i++; s.erase(0, i); }
  long toInt() const { return atol(s.c_str()); }
  String substring(size_t a) const { String r; r.s = s.substr(a); return r; }
  void replace(const char* a, const char* b) { size_t p; while ((p = s.find(a)) != std::string::npos) s.replace(p, strlen(a), b); }
  String readString() { return *this; }
  int indexOf(char c, int from = 0) const { size_t p = s.find(c, from); return p == std::string::npos ? -1 : int(p); }
  String substring(size_t a, size_t b) const { String r; r.s = s.substr(a, b - a); return r; }
  void setCharAt(size_t i, char c) { s[i] = c; }
  size_t write(uint8_t c) { s += char(c); return 1; }
  size_t write(const uint8_t* p, size_t n) { s.append(reinterpret_cast<const char*>(p), n); return n; }
};
inline String operator+(const String& a, const String& b) { String r; r.s = a.s + b.s; return r; }
inline String operator+(const String& a, const char* b) { String r; r.s = a.s + b; return r; }
inline String operator+(const char* a, const String& b) { String r; r.s = std::string(a) + b.s; return r; }
inline String operator+(const String& a, char b) { String r; r.s = a.s + b; return r; }
inline String operator+(const String& a, int b) { return a + String(b); }
inline bool operator==(const String& a, const char* b) { return a.s == b; }
struct SerialT { void begin(int) {} void println(const String& s) { puts(s.c_str()); } void println(const char* s) { puts(s); } } inline Serial;
inline uint32_t millis() { return 0; }
inline void delay(uint32_t) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return 1; }
inline void configTime(long, long, const char*, const char* = nullptr) {}
struct EspT { void restart() {} } inline ESP;
