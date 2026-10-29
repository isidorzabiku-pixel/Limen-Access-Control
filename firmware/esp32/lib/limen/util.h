// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#pragma once
#include <cstdint>
#include <cstdlib>
#include <string>
#include <vector>

namespace gk {

inline std::vector<std::string> split(const std::string& s, char sep) {
  std::vector<std::string> r; std::string cur;
  for (char c : s) { if (c == sep) { r.push_back(cur); cur.clear(); } else cur += c; }
  r.push_back(cur);
  return r;
}

inline bool parseInt(const std::string& s, int64_t& out) {
  if (s.empty()) return false;
  size_t i = (s[0] == '-' || s[0] == '+') ? 1 : 0;
  if (i >= s.size()) return false;
  for (size_t k = i; k < s.size(); k++) if (s[k] < '0' || s[k] > '9') return false;
  out = strtoll(s.c_str(), nullptr, 10);
  return true;
}

inline bool chiaveValida(const std::string& k) {   // ^[0-9a-f]{16,64}$
  if (k.size() < 16 || k.size() > 64) return false;
  for (char c : k) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}

inline bool ipValido(const std::string& s) {      // IPv4 puntato: 4 numeri 0-255
  int campi = 0, valore = -1;
  for (char c : s) {
    if (c >= '0' && c <= '9') { valore = (valore < 0 ? 0 : valore) * 10 + (c - '0'); if (valore > 255) return false; }
    else if (c == '.' && valore >= 0) { campi++; valore = -1; }
    else return false;
  }
  return valore >= 0 && campi == 3;
}

}  // namespace gk
