// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Slave di prova per PC: stesso core del firmware ESP32, rete UDP vera (porta 57990) e file al posto del flash.
// Comandi da stdin, uno per riga:  badge <codice> [lettore]   input <n> <0|1>   status   quit
// Uso: host_slave <id> <cartella_stato> [--no-clock] [--hb=<sec>] [--listen=<porta>] [--master-port=<porta>]
// (le due porte servono solo per provare master e slave sulla stessa macchina: in produzione sono entrambe 57990)
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <sys/stat.h>
#include "slave.h"

using namespace gk;

struct HalPc : Hal {
  int sock = -1;
  std::string dir;
  bool senzaOrologio = false;
  bool ingresso[2] = {true, true};     // livello del pin (a riposo alto: pull-up)
  bool presente[2] = {true, true};
  std::chrono::steady_clock::time_point t0 = std::chrono::steady_clock::now();

  uint64_t millis() override { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(); }
  int64_t unixTime() override {
    return senzaOrologio ? int64_t(millis() / 1000) : int64_t(time(nullptr));
  }
  int portaMaster = PORTA_UDP;
  void unicast(const std::string& ip, const uint8_t* d, size_t n) override {
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(portaMaster); a.sin_addr.s_addr = inet_addr(ip.c_str());
    sendto(sock, d, n, 0, reinterpret_cast<sockaddr*>(&a), sizeof(a));
  }
  void setRelay(int i, bool on) override { printf("RELAY %d %s\n", i + 1, on ? "on" : "off"); fflush(stdout); }
  void setLed(int q, bool on) override { printf("LED %s %s\n", q == 0 ? "verde" : "rosso", on ? "on" : "off"); fflush(stdout); }
  bool readInput(int i, bool& alto) override { if (i < 0 || i > 1 || !presente[i]) return false; alto = ingresso[i]; return true; }
  bool load(const char* nome, std::string& out) override {
    std::ifstream f(dir + "/" + nome, std::ios::binary);
    if (!f) return false;
    std::stringstream ss; ss << f.rdbuf(); out = ss.str(); return true;
  }
  bool save(const char* nome, const std::string& dati) override {
    std::ofstream f(dir + "/" + nome + ".tmp", std::ios::binary | std::ios::trunc);
    f << dati; f.close();
    return rename((dir + "/" + nome + ".tmp").c_str(), (dir + "/" + nome).c_str()) == 0;
  }
  void log(int livello, const std::string& m) override { printf("LOG %d %s\n", livello, m.c_str()); fflush(stdout); }
  std::string randomHex(int byte) override {
    static std::random_device rd; std::string s; const char* H = "0123456789abcdef";
    for (int i = 0; i < byte; i++) { unsigned v = rd() & 255; s += H[v >> 4]; s += H[v & 15]; }
    return s;
  }
};

int main(int argc, char** argv) {
  if (argc < 3) { fprintf(stderr, "uso: %s <id> <cartella> [--no-clock] [--hb=sec]\n", argv[0]); return 2; }
  HalPc hal; hal.dir = argv[2];
  mkdir(argv[2], 0755);
  int hb = 3, ascolto = PORTA_UDP;
  for (int i = 3; i < argc; i++) {
    if (!strcmp(argv[i], "--no-clock")) hal.senzaOrologio = true;
    else if (!strncmp(argv[i], "--hb=", 5)) hb = atoi(argv[i] + 5);
    else if (!strncmp(argv[i], "--listen=", 9)) ascolto = atoi(argv[i] + 9);
    else if (!strncmp(argv[i], "--master-port=", 14)) hal.portaMaster = atoi(argv[i] + 14);
    else if (!strcmp(argv[i], "--no-input2")) hal.presente[1] = false;
    else if (!strcmp(argv[i], "--no-input1")) hal.presente[0] = false;
    else if (!strncmp(argv[i], "--in0=", 6)) hal.ingresso[0] = atoi(argv[i] + 6) != 0;
    else if (!strncmp(argv[i], "--in1=", 6)) hal.ingresso[1] = atoi(argv[i] + 6) != 0;
  }
  hal.sock = socket(AF_INET, SOCK_DGRAM, 0);
  int uno = 1;
  setsockopt(hal.sock, SOL_SOCKET, SO_REUSEADDR, &uno, sizeof(uno));
  setsockopt(hal.sock, SOL_SOCKET, SO_REUSEPORT, &uno, sizeof(uno));
  int rcv = 262144; setsockopt(hal.sock, SOL_SOCKET, SO_RCVBUF, &rcv, sizeof(rcv));
  sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(ascolto); a.sin_addr.s_addr = INADDR_ANY;
  if (bind(hal.sock, reinterpret_cast<sockaddr*>(&a), sizeof(a)) < 0) { perror("bind"); return 1; }

  Slave slave(hal, uint32_t(atoi(argv[1])), hb);
  slave.begin();
  printf("READY\n"); fflush(stdout);

  std::string riga;
  pollfd pf[2] = {{hal.sock, POLLIN, 0}, {0, POLLIN, 0}};
  uint8_t buf[2048];
  for (;;) {
    int r = poll(pf, 2, 10);
    if (r > 0 && (pf[0].revents & POLLIN)) {
      for (;;) {
        sockaddr_in da{}; socklen_t dl = sizeof(da);
        ssize_t n = recvfrom(hal.sock, buf, sizeof(buf), MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&da), &dl);
        if (n <= 0) break;
        slave.onDatagram(buf, size_t(n), inet_ntoa(da.sin_addr));
      }
    }
    if (r > 0 && (pf[1].revents & POLLIN)) {
      char c[512]; ssize_t n = read(0, c, sizeof(c));
      if (n <= 0) break;
      riga.append(c, size_t(n));
      size_t p;
      while ((p = riga.find('\n')) != std::string::npos) {
        std::string cmd = riga.substr(0, p); riga.erase(0, p + 1);
        std::istringstream is(cmd); std::string v; is >> v;
        if (v == "quit") return 0;
        if (v == "badge") { std::string cod; int l = 0; is >> cod >> l; EsitoBadge e = slave.badge(cod, l);
          printf("BADGE %s %s\n", e.ignorato ? "ignorato" : (e.concesso ? "si" : "no"), e.esito.c_str()); fflush(stdout); }
        else if (v == "input") { int n, lv; is >> n >> lv; if (n >= 0 && n < 2) hal.ingresso[n] = lv != 0; printf("OK\n"); fflush(stdout); }
        else if (v == "status") { printf("STATUS %s\n", slave.statoJson().c_str()); fflush(stdout); }
        else if (v == "forget") { slave.dimenticaAbbinamento(); printf("OK\n"); fflush(stdout); }
      }
    }
    slave.loop();
  }
  return 0;
}
