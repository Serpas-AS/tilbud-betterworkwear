"""
Genererer Word-dokumentet fra maler/tilbud-mal.docx.

Word og ikke PDF med vilje: selgeren skal kunne justere dokumentet for det
sendes til kunde. Malen er en helt vanlig .docx som kan apnes og endres i
Word - layout og profil bor der, ikke i denne fila.
"""
import logging
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List

from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage

from api import bilder, dataark, katalog

MAL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "maler", "tilbud-mal.docx")
BILDEBREDDE = Mm(16)   # passer i Varenr.-kolonnen, som er 24 mm bred
UTKATALOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "generert")
GYLDIGHET_DAGER = 30


def kr(v) -> str:
    """Norsk tallformat: 1 234,50 - mellomrom som tusenskille, komma som desimal."""
    d = Decimal(str(v)).quantize(Decimal("0.01"))
    heltall, desimal = f"{d:.2f}".split(".")
    negativ = heltall.startswith("-")
    heltall = heltall.lstrip("-")
    grupper = []
    while len(heltall) > 3:
        grupper.insert(0, heltall[-3:])
        heltall = heltall[:-3]
    grupper.insert(0, heltall)
    return ("-" if negativ else "") + " ".join(grupper) + "," + desimal


def _rabatt_tekst(l: Dict) -> str:
    if l["rabatt_type"] == "prosent" and l.get("rabatt_prosent") is not None:
        # IKKE Decimal.normalize() her: den gjor 10 om til 1E+1.
        p = f'{Decimal(str(l["rabatt_prosent"])):.2f}'.rstrip("0").rstrip(".")
        return f"\n({p} % rabatt)"
    if l["rabatt_type"] == "fastpris":
        return "\n(avtalt fastpris)"
    return ""


def _trygg_miniatyr(sti):
    """Tynn innpakning, slik at en feil i bildeleddet aldri velter et tilbud."""
    try:
        return bilder.miniatyr(sti)
    except Exception:
        return None


def _bilde(doc: DocxTemplate, l: Dict):
    """
    Produktbilde til varelinja, eller tom streng.

    Tom streng og ikke None: docxtpl skriver 'None' i dokumentet for en
    None-verdi, mens tom streng blir ingenting.

    Bildet hentes fra `l["bilde"]`, som ble fryst da tilbudet ble laget - ikke
    slatt opp i katalogen na. Er bildet byttet ut i butikken i mellomtiden,
    viser en regenerering fortsatt det kunden fikk se.
    """
    sti = _trygg_miniatyr(l.get("bilde"))
    if not sti:
        return ""
    return InlineImage(doc, sti, width=BILDEBREDDE)


def generer(tilbud: Dict, linjer: List[Dict], selger: Dict) -> str:
    """
    Skriver dokumentet og returnerer stien.

    Alle tall kommer ferdig frosne fra databasen. Ingenting regnes om her -
    dokumentet skal vise nøyaktig det som ble lagret da tilbudet ble laget.
    """
    doc = DocxTemplate(MAL)
    laget = tilbud.get("created_at") or date.today()
    laget_dato = laget.date() if hasattr(laget, "date") else laget

    doc.render({
        "tilbudsnr": tilbud["tilbudsnr"],
        "dato": laget_dato.strftime("%d.%m.%Y"),
        "gyldig_til": (laget_dato + timedelta(days=GYLDIGHET_DAGER)).strftime("%d.%m.%Y"),
        "kunde_firma": tilbud.get("kunde_firma") or "",
        "kunde_kontakt": tilbud.get("kunde_kontakt") or "",
        "kunde_epost": tilbud.get("kunde_epost") or "",
        "kunde_telefon": tilbud.get("kunde_telefon") or "",
        "selger_navn": selger.get("navn") or selger["email"],
        "selger_epost": selger["email"],
        "selger_telefon": selger.get("telefon") or "",
        "linjer": [{
            "bilde": _bilde(doc, l),
            "sku": l["sku"],
            "navn": l["navn"],
            "rabatt_tekst": _rabatt_tekst(l),
            "antall": str(l["antall"]),
            "listepris": kr(l["listepris"]),
            "nettopris": kr(l["nettopris"]),
            "linjesum": kr(l["linjesum"]),
        } for l in linjer],
        "sum_eks_mva": kr(tilbud["sum_eks_mva"]),
        "sum_mva": kr(tilbud["sum_mva"]),
        "sum_inkl_mva": kr(tilbud["sum_inkl_mva"]),
        "notat": tilbud.get("notat") or "",
    # autoescape: verdiene settes inn som tekst, ikke rå Word-XML. Uten dette forsvant «&» («Hansen & Sønn»),
    # og en selger kunne legge inn vilkårlig markup i dokumentet som går til kunden (03.10.2026).
    }, autoescape=True)

    # Databladene legges til ETTER render(), pa det ferdig utfylte dokumentet.
    # Antall ark varierer per tilbud, og felter mangler ofte - det lar seg ikke
    # uttrykke rimelig i .docx-malen. Se api/dataark.py.
    try:
        ids = [l["produkt_id"] for l in linjer if l.get("produkt_id")]
        dataark.legg_til(doc.docx, linjer, katalog.dataark(ids), kr)
    except Exception:
        # Databladene er et tillegg. Feiler de, skal tilbudet likevel bli til -
        # prisene er det som betyr noe, og selgeren merker et manglende ark.
        logging.exception("Klarte ikke legge til produktdatablad")

    os.makedirs(UTKATALOG, exist_ok=True)
    sti = os.path.join(UTKATALOG, f"{tilbud['tilbudsnr']}.docx")
    doc.save(sti)
    return sti
