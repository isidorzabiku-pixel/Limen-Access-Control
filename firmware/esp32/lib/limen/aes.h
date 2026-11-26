// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// AES-128 dalla libreria crittografica della piattaforma (mbedTLS sull'ESP32, OpenSSL sul PC per le prove):
// niente AES scritto in casa. Sopra il blocco: CBC e CMAC (NIST SP 800-38B / RFC 4493).
#pragma once
#include <cstddef>
#include <cstdint>

namespace gk {
namespace aes {

void cifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]);
void decifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]);

// CBC senza padding (n multiplo di 16). iv viene aggiornato con l'ultimo blocco cifrato (catena).
void cbcCifra(const uint8_t chiave[16], uint8_t iv[16], const uint8_t* in, size_t n, uint8_t* out);
void cbcDecifra(const uint8_t chiave[16], uint8_t iv[16], const uint8_t* in, size_t n, uint8_t* out);

// AES-CMAC (16 byte).
void cmac(const uint8_t chiave[16], const uint8_t* msg, size_t n, uint8_t out[16]);

}  // namespace aes
}  // namespace gk
