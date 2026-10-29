# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Genera i manuali PDF (limen-manuale-it.pdf, limen-manuale-en.pdf) in backend/docs/.
Contenuto qui sotto; le immagini sono in img/ (schermate di un'istanza di prova con dati finti).
Se la webapp cambia, aggiorna il testo (e le immagini) e rilancia: python genera_manuale.py  (serve Playwright con Chromium)."""
import base64
from pathlib import Path
from playwright.sync_api import sync_playwright

QUI = Path(__file__).resolve().parent
FONTS = QUI.parent.parent / "static" / "fonts"

def img(lang, nome, didascalia):
    b64 = base64.b64encode((QUI / "img" / f"{lang}-{nome}.jpg").read_bytes()).decode()
    return f'<figure><img src="data:image/jpeg;base64,{b64}" alt=""><figcaption>{didascalia}</figcaption></figure>'

LOGO = '<svg viewBox="0 0 64 64" fill="none" stroke="#e6edf7" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12v38h26"/><circle cx="40" cy="30" r="3.8" fill="#38bdf8" stroke="none"/></svg>'

CSS = """
@font-face { font-family: "Inter"; src: url("FONTS/inter-var.woff") format("woff"); font-weight: 100 900; }
:root { --navy:#0b1222; --ink:#1b2437; --muted:#5d6b82; --line:#dfe5ee; --cyan:#0ea5e9; --soft:#f4f7fb; --warn:#b45309; --warn-soft:#fdf1df; }
@page { size: A4; margin: 20mm 20mm 20mm 20mm; @bottom-left { content: "FOOT"; font: 500 8pt Inter, sans-serif; color:#8b9bb4; } @bottom-right { content: counter(page); font: 500 8pt Inter, sans-serif; color:#8b9bb4; } }
@page :first { margin: 0; @bottom-left { content: none; } @bottom-right { content: none; } }
* { box-sizing: border-box; } html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin:0; font: 400 10pt/1.55 Inter, sans-serif; color: var(--ink); }
.cover { height:297mm; width:210mm; background:var(--navy); color:#e2e8f0; padding:26mm 24mm; position:relative; overflow:hidden; page-break-after:always; break-after:page; }
.cover::after { content:""; position:absolute; left:0; bottom:0; width:100%; height:3mm; background:linear-gradient(90deg,#38bdf8,rgba(56,189,248,0)); }
.logo { display:flex; align-items:center; gap:4mm; font-weight:700; font-size:16pt; color:#fff; } .logo svg { width:14mm; height:14mm; }
.cover h1 { margin:80mm 0 0; font-size:42pt; line-height:1.04; letter-spacing:-.03em; color:#fff; } .cover h1 span { color:#38bdf8; }
.cover .sub { margin-top:8mm; max-width:120mm; font-size:13pt; line-height:1.5; color:#a9b8d0; }
h2 { font-size:17pt; letter-spacing:-.02em; margin:0 0 3mm; padding-top:2mm; break-after:avoid; } h2 .n { color:var(--cyan); margin-right:2mm; }
h3 { font-size:11pt; margin:6mm 0 1.5mm; break-after:avoid; }
p { margin:0 0 3mm; } ul, ol { margin:0 0 3mm; padding-left:5mm; } li { margin:0 0 1.2mm; }
section { break-before: page; } section.first { break-before: auto; }
b.ui { font-weight:600; background:var(--soft); border:1px solid var(--line); border-radius:3px; padding:0 1.4mm; white-space:nowrap; }
figure { margin:4mm 0 5mm; break-inside:avoid; } figure img { width:100%; border:1px solid var(--line); border-radius:3mm; display:block; }
figcaption { font-size:8.5pt; color:var(--muted); margin-top:1.5mm; }
.note { background:var(--soft); border-left:1mm solid var(--cyan); padding:2.5mm 4mm; margin:3mm 0 4mm; border-radius:0 2mm 2mm 0; break-inside:avoid; }
.warn { background:var(--warn-soft); border-left-color:var(--warn); }
table { border-collapse:collapse; width:100%; margin:2mm 0 4mm; font-size:9.5pt; } th, td { text-align:left; vertical-align:top; padding:2mm 3mm; border-bottom:1px solid var(--line); } th { background:var(--soft); font-weight:600; }
tr { break-inside:avoid; }
"""

def pagina(lang, c):
    ui = lambda s: f'<b class="ui">{s}</b>'
    html = f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>Limen — {c["titolo"]}</title><style>{CSS.replace("FONTS", FONTS.as_posix()).replace("FOOT", "Limen · " + c["titolo"])}</style></head><body>'
    html += f'<div class="cover"><div class="logo">{LOGO}Limen</div><h1>{c["h1"]}</h1><div class="sub">{c["sub"]}</div></div>'
    for i, (titolo, corpo) in enumerate(c["sezioni"](ui, lambda n, d: img(lang, n, d)), 1):
        html += f'<section class="{"first" if i == 1 else ""}"><h2><span class="n">{i}</span>{titolo}</h2>{corpo}</section>'
    return html + "</body></html>"

from contenuti import IT, EN  # noqa: E402

if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch()
        for lang, c in (("it", IT), ("en", EN)):
            f = QUI / f"_manuale_{lang}.html"
            f.write_text(pagina(lang, c), encoding="utf-8")
            pg = b.new_page(); pg.goto(f.as_uri()); pg.evaluate("document.fonts.ready")
            out = QUI.parent / f"limen-manuale-{lang}.pdf"
            pg.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
            f.unlink(); print("Creato", out)
        b.close()
