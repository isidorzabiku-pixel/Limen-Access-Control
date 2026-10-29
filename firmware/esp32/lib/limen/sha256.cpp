// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "sha256.h"
#include <cstring>

namespace gk {
namespace {

const uint32_t K[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

struct Sha256 {
  uint32_t h[8];
  uint8_t buf[64];
  size_t nbuf = 0;
  uint64_t totale = 0;

  Sha256() {
    const uint32_t iv[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(h, iv, sizeof(h));
  }
  void blocco(const uint8_t* p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
      w[i] = (uint32_t(p[4*i]) << 24) | (uint32_t(p[4*i+1]) << 16) | (uint32_t(p[4*i+2]) << 8) | uint32_t(p[4*i+3]);
    for (int i = 16; i < 64; i++) {
      uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
      uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
      w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=h[0], b=h[1], c=h[2], d=h[3], e=h[4], f=h[5], g=h[6], hh=h[7];
    for (int i = 0; i < 64; i++) {
      uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      uint32_t ch = (e & f) ^ (~e & g);
      uint32_t t1 = hh + S1 + ch + K[i] + w[i];
      uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
      uint32_t t2 = S0 + mj;
      hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d; h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=hh;
  }
  void aggiungi(const uint8_t* p, size_t n) {
    totale += n;
    while (n > 0) {
      size_t k = 64 - nbuf; if (k > n) k = n;
      memcpy(buf + nbuf, p, k); nbuf += k; p += k; n -= k;
      if (nbuf == 64) { blocco(buf); nbuf = 0; }
    }
  }
  void fine(uint8_t out[32]) {
    uint64_t bit = totale * 8;
    uint8_t uno = 0x80; aggiungi(&uno, 1);
    uint8_t zero = 0; while (nbuf != 56) aggiungi(&zero, 1);
    uint8_t len[8]; for (int i = 0; i < 8; i++) len[i] = uint8_t(bit >> (56 - 8 * i));
    aggiungi(len, 8);
    for (int i = 0; i < 8; i++) { out[4*i]=h[i]>>24; out[4*i+1]=h[i]>>16; out[4*i+2]=h[i]>>8; out[4*i+3]=h[i]; }
  }
};

void hmac(const std::string& chiave, const uint8_t* dati, size_t n, uint8_t out[32]) {
  uint8_t k[64] = {0};
  if (chiave.size() > 64) {
    Sha256 s; s.aggiungi(reinterpret_cast<const uint8_t*>(chiave.data()), chiave.size()); s.fine(k);
  } else {
    memcpy(k, chiave.data(), chiave.size());
  }
  uint8_t ipad[64], opad[64];
  for (int i = 0; i < 64; i++) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }
  uint8_t interno[32];
  Sha256 a; a.aggiungi(ipad, 64); a.aggiungi(dati, n); a.fine(interno);
  Sha256 b; b.aggiungi(opad, 64); b.aggiungi(interno, 32); b.fine(out);
}

std::string esa(const uint8_t* p, size_t n) {
  static const char* H = "0123456789abcdef";
  std::string s; s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) { s += H[p[i] >> 4]; s += H[p[i] & 15]; }
  return s;
}

}  // namespace

std::string hmacHex32(const std::string& chiave, const uint8_t* dati, size_t n) {
  uint8_t out[32]; hmac(chiave, dati, n, out);
  return esa(out, 16);
}

std::string hmacHex64(const std::string& chiave, const std::string& dati) {
  uint8_t out[32]; hmac(chiave, reinterpret_cast<const uint8_t*>(dati.data()), dati.size(), out);
  return esa(out, 32);
}

bool uguali(const std::string& a, const std::string& b) {
  if (a.size() != b.size()) return false;
  uint8_t d = 0;
  for (size_t i = 0; i < a.size(); i++) d |= uint8_t(a[i] ^ b[i]);
  return d == 0;
}

}  // namespace gk
