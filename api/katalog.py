"""
Leser produkter og priser fra den nye nettbutikk-katalogen (`bww_web`).

Byttet fra Magento (`bbw`) 03.10.2026, da betterworkwear.no gikk over til
FastAPI (/var/www/ny.betterworkwear.no). `bww_web` er der produktene og
prisene vedlikeholdes nå. Den gamle Magento-versjonen av modulen ligger i
/root/tilbud-bytte-2026-10-03/tilbud-api-for.tgz.

Identiteten er `bww_web.products.id` (`produkt_id`), ikke Magento-ID. Produkter
som opprettes i ny admin har ingen Magento-ID, og WRKS-plaggene kommer fra
wrks-Magento, så deres `magento_id` er en ANNEN butikks ID. Linjer laget før
byttet har `magento_id` (bbw) og fikk `produkt_id` fylt inn via SKU.

Prisene speiler det Magento-versjonen gjorde, slik at et tilbud blir likt
før og etter byttet (verifisert produkt for produkt 03.10.2026):

* Enkle produkter: `products.price`.
* Konfigurerbare: listepris = høyeste variantpris, kampanjepris = laveste.
  Det var også Magento-modulens regel (høyeste ordinærpris, laveste faktiske
  pris over variantene). `prisvariasjon` er sann når de er ulike.
  Bare aktive varianter teller, slik Magentos prisindeks bare tok med
  kjøpbare barn.

VIKTIG om mva: katalogprisene er INKLUSIVE mva, som i Magento og på nettsiden.
Et bedriftstilbud vises eks. mva, så modulen returnerer alltid EKS mva.
`bww_web` har ingen mva-klasser; alle 4 472 prisrader i Magento lå i samme
klasse (25 %). Satsen er derfor én konstant, men den leses fra miljøet
(`KATALOG_MVA_SATS`) og ikke gjemt som `/ 1.25` i regnestykket. Butikken har
blitt brent på hardkodet `/ 1.25` før (Serpas_CmsProductBox, PrebuiltCart).
"""
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional

from api.db import katalog_db

MVA_SATS = Decimal(os.environ.get("KATALOG_MVA_SATS", "25"))


def _q2(v: Decimal) -> Decimal:
    """Avrunder til øre, kaufmännisch (0,5 opp) - ikke bankers rounding."""
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _eks_mva(brutto: Decimal, sats: Decimal) -> Decimal:
    if sats <= 0:
        return _q2(brutto)
    return _q2(brutto / (Decimal("1") + sats / Decimal("100")))


# Felles utvalg. Variantprisene og første bilde hentes i samme spørring
# (korrelerte underspørringer på indekserte product_id), ikke ett kall per rad.
_UTVALG = """
    SELECT p.id, p.sku, p.name AS navn, p.type, p.price,
           (SELECT MAX(v.price) FROM product_variants v
             WHERE v.product_id = p.id AND v.is_active = 1) AS maks_pris,
           (SELECT MIN(v.price) FROM product_variants v
             WHERE v.product_id = p.id AND v.is_active = 1) AS min_pris,
           (SELECT i.filename FROM product_images i
             WHERE i.product_id = p.id ORDER BY i.sort_order, i.id LIMIT 1) AS bilde
      FROM products p
"""


def _berik(rows: List[Dict]) -> List[Dict]:
    """Gjør databaserader om til ferdige produktdikter med priser EKS mva."""
    ut = []
    for r in rows:
        prisvariasjon = False
        if r["type"] == "configurable" and r["maks_pris"] is not None:
            brutto_liste = Decimal(str(r["maks_pris"]))
            brutto_kampanje = Decimal(str(r["min_pris"]))
            prisvariasjon = r["min_pris"] != r["maks_pris"]
        else:
            brutto_liste = brutto_kampanje = Decimal(str(r["price"] or 0))

        ut.append({
            "produkt_id": r["id"],
            "sku": r["sku"],
            "navn": r["navn"] or r["sku"],
            "type": r["type"],
            "listepris": _eks_mva(brutto_liste, MVA_SATS),
            "kampanjepris": _eks_mva(brutto_kampanje, MVA_SATS),
            "mva_sats": MVA_SATS,
            "prisvariasjon": prisvariasjon,
            # Sti under media/catalog/product, f.eks. '/w/r/wr10096-....png' eller
            # '/wrks/w/r/…' for WRKS-plagg. Oversettes av api/bilder.py.
            "bilde": r["bilde"] or None,
        })
    return ut


def sok(term: str, limit: int = 25) -> List[Dict]:
    """
    Søker på SKU eller navn blant aktive produkter. Returnerer priser EKS mva.

    STØRRELSESVARIANTER ER UTELATT, som før: et tilbud gis på plagget, ikke på
    «Kaolin College genser str. L». I `bww_web` er det gratis - variantene
    ligger i `product_variants`, og `products` er nøyaktig plaggene og de
    frittstående varene (hansker, tilbehør) som vises i nettbutikken.

    `hent()` har med vilje ikke aktiv-filteret: et produkt som er tatt av
    nettsiden, skal fortsatt kunne vises i et tilbud som allerede er laget.
    """
    term = (term or "").strip()
    if len(term) < 2:
        return []

    conn = katalog_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            _UTVALG + """
             WHERE p.is_active = 1 AND (p.sku LIKE %s OR p.name LIKE %s)
             ORDER BY (p.sku = %s) DESC, p.name
             LIMIT %s""",
            (f"%{term}%", f"%{term}%", term, limit),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return _berik(rows)


def dataark(produkt_ids: List[int]) -> Dict[int, Dict[str, str]]:
    """
    Beskrivende produktdata til databladene bakerst i tilbudet.

    Returnerer {produkt_id: {description, teknisk_beskrivelse, vaskeanvisning, ean}}.
    Verdiene er HTML - de vaskes i api/dataark.py. Tomme felt utelates, så de
    ikke står som tomme overskrifter på arket.

    Slås opp NÅ, ikke fra frosne data på tilbudslinja: prisen er en forpliktelse
    og fryses (se prising.py), en produktbeskrivelse er det ikke.

    EAN: Magento hadde den som attributt på selve produktet. I `bww_web` ligger
    den på variantraden, og et enkelt produkt har én variant med samme
    Magento-ID som seg selv - den brukes.
    """
    if not produkt_ids:
        return {}

    ut: Dict[int, Dict[str, str]] = {i: {} for i in produkt_ids}
    plassholdere = ",".join(["%s"] * len(produkt_ids))
    conn = katalog_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT p.id, p.description, p.teknisk_beskrivelse, p.vaskeanvisning,
                       (SELECT v.ean FROM product_variants v
                         WHERE v.product_id = p.id AND v.magento_id = p.magento_id
                           AND v.ean <> '' LIMIT 1) AS ean
                  FROM products p
                 WHERE p.id IN ({plassholdere})""",
            tuple(produkt_ids),
        )
        for r in cur.fetchall():
            for felt in ("description", "teknisk_beskrivelse", "vaskeanvisning", "ean"):
                if r[felt]:
                    ut[r["id"]][felt] = r[felt]
    finally:
        conn.close()
    return ut


def eksportfakta(produkt_ids: List[int]) -> Dict[int, Dict]:
    """
    Det Excel-eksporten trenger utover tilbudslinja (api/eksport.py):
    {produkt_id: {slug, kategori, storrelser: [...], farger: [...]}}.

    Størrelser og farger fra aktive varianter, i variantenes rekkefølge.
    Fargen er `color_group` («Gul», «Oransje»), ikke leverandørens fargenavn
    («Safety Yellow»). Slås opp nå, som databladene: det er produktfakta,
    ikke en del av prisforpliktelsen.
    """
    if not produkt_ids:
        return {}
    plassholdere = ",".join(["%s"] * len(produkt_ids))
    ut: Dict[int, Dict] = {}
    conn = katalog_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT p.id, p.slug, c.name AS kategori
                  FROM products p LEFT JOIN categories c ON c.id = p.category_id
                 WHERE p.id IN ({plassholdere})""", tuple(produkt_ids))
        for r in cur.fetchall():
            ut[r["id"]] = {"slug": r["slug"], "kategori": r["kategori"] or "",
                           "storrelser": [], "farger": []}
        cur.execute(
            f"""SELECT product_id, size, color_group FROM product_variants
                 WHERE product_id IN ({plassholdere}) AND is_active = 1
                 ORDER BY product_id, sort_order, id""", tuple(produkt_ids))
        for r in cur.fetchall():
            f = ut.get(r["product_id"])
            if not f:
                continue
            for felt, verdi in (("storrelser", r["size"]), ("farger", r["color_group"])):
                if verdi and verdi not in f[felt]:
                    f[felt].append(verdi)
    finally:
        conn.close()
    return ut


def hent(produkt_id: int) -> Optional[Dict]:
    """Ett produkt, samme form som sok(). Brukes når en linje legges til."""
    conn = katalog_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(_UTVALG + " WHERE p.id = %s", (produkt_id,))
        r = cur.fetchone()
    finally:
        conn.close()
    return _berik([r])[0] if r else None
