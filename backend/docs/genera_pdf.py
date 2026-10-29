# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Genera limen-api.pdf da api_docs.html (serve Playwright con Chromium). Uso: python genera_pdf.py"""
from pathlib import Path
from playwright.sync_api import sync_playwright

qui = Path(__file__).resolve().parent
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto((qui / "api_docs.html").as_uri())
    pg.evaluate("document.fonts.ready")
    pg.pdf(path=str(qui / "limen-api.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
    b.close()
print("Creato", qui / "limen-api.pdf")
