"""
Excel-eksport av et tilbud, i samme kolonneoppsett som regnearkene som
importeres i tilbudsløsningen på den andre serveren (mal:
/home/magento/ny-better/THORSTEINSEN.xlsx, 21.10.2026).

Bare for brukerne i `EKSPORT_TILGANG` - sjekkes i ruta OG på knappen.

Tallene kommer fra de FROSNE tilbudslinjene, som Word-dokumentet:

* Veil. pris  = listepris, eks. mva (malen har 1999 kr inkl. som 1599,2).
* Kundepris   = nettopris, eks. mva.
* Rabatt      = prosent i forhold til veil. pris, ikke selgerens prosent.
  Malen regner Kundepris = Veil. pris × (1 − Rabatt/100), og det skal stemme
  også når linja har fastpris eller en kampanjepris i bunnen.

Størrelser, farge, kategori og lenke slås opp i katalogen nå. Varer utenfor
nettbutikken har bare navn, nummer, priser og leverandørlenken.
"""
import io
import os
from decimal import Decimal
from typing import Dict

from openpyxl import Workbook
from openpyxl.styles import Font

from api import bilder, katalog

EKSPORT_TILGANG = {
    e.strip().lower()
    for e in os.environ.get("TILBUD_EKSPORT_EPOST", "morten@serpas.no").split(",")
    if e.strip()
}
BUTIKK_URL = os.environ.get("BUTIKK_URL", "https://www.betterworkwear.no").rstrip("/")

KOLONNER = [  # (overskrift, bredde) - som i malen
    ("Produktnavn", 29.7), ("Produkt nr.", 10.8), ("Link til varen", 78.7),
    ("Antall", 5.8), ("Veil. pris", 8), ("Rabatt", 6.7), ("Kundepris", 9.2),
    ("Størrelser klærne går i", 18.7), ("Farge:", 6.2), ("Kategori:", 9.3),
    ("Bildeurl", 7.5),
]


def har_tilgang(bruker: Dict) -> bool:
    return (bruker.get("email") or "").lower() in EKSPORT_TILGANG


def _storrelser(liste) -> str:
    """«44-62» som i malen. Én størrelse står alene, to blir «S, M»."""
    if len(liste) > 2:
        return f"{liste[0]}-{liste[-1]}"
    return ", ".join(liste)


def _rabatt(liste: Decimal, netto: Decimal):
    if liste <= 0 or netto >= liste:
        return 0
    return float(round((1 - netto / liste) * 100, 2))


def lag_xlsx(t: Dict) -> bytes:
    """`t` er et tilbud fra tjeneste.hent_tilbud(), med `linjer`."""
    fakta = katalog.eksportfakta([l["produkt_id"] for l in t["linjer"] if l.get("produkt_id")])

    wb = Workbook()
    ark = wb.active
    ark.title = t["tilbudsnr"]
    ark.append([k for k, _ in KOLONNER])
    for celle in ark[1]:
        celle.font = Font(bold=True)
    for i, (_, bredde) in enumerate(KOLONNER):
        ark.column_dimensions[chr(ord("A") + i)].width = bredde

    for l in t["linjer"]:
        f = fakta.get(l.get("produkt_id")) or {}
        liste = Decimal(str(l["listepris"]))
        netto = Decimal(str(l["nettopris"]))
        if f.get("slug"):
            lenke = f"{BUTIKK_URL}/produkt/{f['slug']}"
        else:
            lenke = l.get("kilde_url") or ""
        ark.append([
            l["navn"], l["sku"], lenke, l["antall"],
            float(liste), _rabatt(liste, netto), float(netto),
            _storrelser(f.get("storrelser", [])),
            ", ".join(f.get("farger", [])),
            f.get("kategori", ""),
            bilder.bilde_url(l.get("bilde")) or l.get("kilde_bilde_url") or "",
        ])
        # openpyxl lagrer en tekst som begynner med «=» som FORMEL. Navn og
        # lenker til varer utenfor nettbutikken kommer fra fremmede nettsider,
        # så de skal aldri kunne bli en formel i regnearket (formelinjeksjon).
        for celle in ark[ark.max_row]:
            if isinstance(celle.value, str) and celle.value.startswith("="):
                celle.data_type = "s"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
