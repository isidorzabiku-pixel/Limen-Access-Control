// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "aes.h"
#include <cstring>

#if defined(ARDUINO) || defined(ESP_PLATFORM)
#include <mbedtls/aes.h>
#else
#include <openssl/evp.h>
#endif

namespace gk {
namespace aes {

#if defined(ARDUINO) || defined(ESP_PLATFORM)

void cifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]) {
  mbedtls_aes_context c;
  mbedtls_aes_init(&c);
  mbedtls_aes_setkey_enc(&c, chiave, 128);
  mbedtls_aes_crypt_ecb(&c, MBEDTLS_AES_ENCRYPT, in, out);
  mbedtls_aes_free(&c);
}

void decifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]) {
  mbedtls_aes_context c;
  mbedtls_aes_init(&c);
  mbedtls_aes_setkey_dec(&c, chiave, 128);
  mbedtls_aes_crypt_ecb(&c, MBEDTLS_AES_DECRYPT, in, out);
  mbedtls_aes_free(&c);
}

#else  // PC: OpenSSL, solo per le prove

static void ecb(bool cifra, const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]) {
  EVP_CIPHER_CTX* c = EVP_CIPHER_CTX_new();
  int n = 0;
  EVP_CipherInit_ex(c, EVP_aes_128_ecb(), nullptr, chiave, nullptr, cifra ? 1 : 0);
  EVP_CIPHER_CTX_set_padding(c, 0);
  EVP_CipherUpdate(c, out, &n, in, 16);
  EVP_CIPHER_CTX_free(c);
}
void cifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]) { ecb(true, chiave, in, out); }
void decifraBlocco(const uint8_t chiave[16], const uint8_t in[16], uint8_t out[16]) { ecb(false, chiave, in, out); }

#endif

void cbcCifra(const uint8_t chiave[16], uint8_t iv[16], const uint8_t* in, size_t n, uint8_t* out) {
  uint8_t b[16];
  for (size_t i = 0; i < n; i += 16) {
    for (int j = 0; j < 16; j++) b[j] = in[i + j] ^ iv[j];
    cifraBlocco(chiave, b, out + i);
    memcpy(iv, out + i, 16);
  }
}

void cbcDecifra(const uint8_t chiave[16], uint8_t iv[16], const uint8_t* in, size_t n, uint8_t* out) {
  uint8_t b[16], prossimo[16];
  for (size_t i = 0; i < n; i += 16) {
    memcpy(prossimo, in + i, 16);
    decifraBlocco(chiave, in + i, b);
    for (int j = 0; j < 16; j++) out[i + j] = b[j] ^ iv[j];
    memcpy(iv, prossimo, 16);
  }
}

static void raddoppia(const uint8_t in[16], uint8_t out[16]) {   // moltiplicazione per x in GF(2^128)
  uint8_t riporto = in[0] & 0x80;
  for (int i = 0; i < 15; i++) out[i] = uint8_t((in[i] << 1) | (in[i + 1] >> 7));
  out[15] = uint8_t(in[15] << 1);
  if (riporto) out[15] ^= 0x87;
}

void cmac(const uint8_t chiave[16], const uint8_t* msg, size_t n, uint8_t out[16]) {
  uint8_t zero[16] = {0}, l[16], k1[16], k2[16];
  cifraBlocco(chiave, zero, l);
  raddoppia(l, k1);
  raddoppia(k1, k2);
  size_t blocchi = n == 0 ? 1 : (n + 15) / 16;
  bool completo = n != 0 && n % 16 == 0;
  uint8_t x[16] = {0}, y[16], ultimo[16];
  for (size_t b = 0; b + 1 < blocchi; b++) {
    for (int j = 0; j < 16; j++) y[j] = x[j] ^ msg[b * 16 + j];
    cifraBlocco(chiave, y, x);
  }
  size_t resto = n - (blocchi - 1) * 16;
  for (int j = 0; j < 16; j++) {
    uint8_t m = size_t(j) < resto ? msg[(blocchi - 1) * 16 + j] : (size_t(j) == resto ? 0x80 : 0x00);
    ultimo[j] = m ^ (completo ? k1[j] : k2[j]);
  }
  for (int j = 0; j < 16; j++) y[j] = x[j] ^ ultimo[j];
  cifraBlocco(chiave, y, out);
}

}  // namespace aes
}  // namespace gk
