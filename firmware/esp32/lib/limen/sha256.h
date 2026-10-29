// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// SHA-256 e HMAC-SHA256 senza dipendenze (stessa firma del master: primi 32 caratteri esadecimali).
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace gk {

// HMAC-SHA256(chiave, dati) in esadecimale minuscolo, troncato a 32 caratteri.
std::string hmacHex32(const std::string& chiave, const uint8_t* dati, size_t n);
inline std::string hmacHex32(const std::string& chiave, const std::string& dati) {
  return hmacHex32(chiave, reinterpret_cast<const uint8_t*>(dati.data()), dati.size());
}
// HMAC completo (64 caratteri), per i test.
std::string hmacHex64(const std::string& chiave, const std::string& dati);
bool uguali(const std::string& a, const std::string& b);   // confronto a tempo costante

}  // namespace gk
