// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Prova di importaConfigTxt() (src/main.cpp) con i finti header: file valido, con errori, chiavi sconosciute.
#include <cassert>
#include "../src/main.cpp"

static void prova(const char* testo) { finto_config() = testo; finto_presente() = true; }

int main() {
  prova("# commento\nid = 7\nwifi_nome = Casa Mia\nwifi_password = pa=ss word\nip = 192.168.1.91\ngateway=192.168.1.1\nsubnet = 255.255.255.0\n"
        "password_admin = segreta\nrele1 = 10\nrele2_attivo_alto = no\nled_verde = 11\nled_rosso = 12\nchiave_strana = 1\r\n");
  importaConfigTxt();
  assert(cfg.presente && cfg.id == 7 && cfg.ssid == "Casa Mia" && cfg.wifiPass == "pa=ss word" && cfg.ip == "192.168.1.91");
  assert(cfg.adminPass == "segreta" && cfg.relePin[0] == 10 && cfg.relePin[1] == 5 && !cfg.releAttivoAlto[1] && cfg.releAttivoAlto[0]);
  assert(cfg.ledPin[0] == 11 && cfg.ledPin[1] == 12);
  assert(!finto_presente());                           // file cancellato dopo l'importazione

  Config prima = cfg;
  prova("id = 0\nwifi_nome = X\n");                    // ID non valido: ignorato e il file resta
  importaConfigTxt(); assert(cfg.id == prima.id && finto_presente());
  prova("id = 3\nwifi_nome = X\nrele1 = 30\n");        // pin 30 e' del flash
  importaConfigTxt(); assert(cfg.id == prima.id && finto_presente());
  prova("id = 3\nwifi_nome = X\nrele1 = 4\nrele2 = 4\n");   // stesso pin due volte
  importaConfigTxt(); assert(cfg.id == prima.id && finto_presente());
  prova("id = 3\nwifi_nome = X\nip = 300.1.1.1\n");
  // (il finto IPAddress accetta tutto: qui si prova solo che il resto non rompa)
  prova("id = 3\nwifi_nome = X\npassword_admin = abc\n");   // password troppo corta
  importaConfigTxt(); assert(cfg.id == prima.id && finto_presente());
  // chiavi in inglese (le italiane sopra restano accettate)
  prova("id = 8\nwifi_name = Home\nwifi_password = x\nip = 192.168.1.92\nadmin_password = secret\nrelay1 = 10\nrelay2_active_high = no\ninput1 = 13\nled_green = 14\nled_red = 15\n");
  importaConfigTxt();
  assert(cfg.id == 8 && cfg.ssid == "Home" && cfg.adminPass == "secret" && cfg.relePin[0] == 10 && !cfg.releAttivoAlto[1] && cfg.ingressoPin[0] == 13 && cfg.ledPin[0] == 14 && cfg.ledPin[1] == 15);
  puts("CONFIG TXT OK");
  return 0;
}
