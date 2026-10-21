"""
Produktdatablad bakerst i tilbudsdokumentet.

Ett ark per vare, etter selve tilbudet, med sideskift foran hvert. Innholdet
hentes fra Magento-katalogen: beskrivelse, teknisk beskrivelse, vaskeanvisning,
EAN og bilde.

Dette bygges i kode og ikke i .docx-malen, i motsetning til resten av
dokumentet. Grunnen er at antall ark og antall avsnitt varierer per tilbud, og
felter mangler ofte - 164 av 439 varer har vaskeanvisning. En mal maatte da
enten hatt tomme overskrifter staaende, eller en loekke som er vanskeligere aa
lese enn koden under. Skriften og stoerrelsene folger malens egne stiler
(Heading 1/2 og Normal), saa profilendringer i malen slaar likevel gjennom.

MVA: prisene som skrives her tas fra den FROSNE tilbudslinja, med samme
etikett som i pristabellen. Google Apps Scriptet som denne tjenesten avloser
gjorde nettopp ikke det - det skrev `price_incl_tax` under overskriften
"Pris eks. mva", saa samme vare sto til to ulike priser i samme dokument.
Se CLAUDE.md §4 i /var/www/bww.
"""
import html as htmllib
import re
from decimal import Decimal
from typing import Dict, List

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from api import bilder

BILDEBREDDE = Mm(65)

# Samme visuelle sprak som malen: ingen rammer, etiketter i sma graa kapitéler,
# og en enkelt harlinje der oyet trenger et skille. Se maler/tilbud-mal.docx.
MORK = RGBColor(0x12, 0x10, 0x0C)
GRA  = RGBColor(0x76, 0x76, 0x76)
HARLIN = "D9D9D9"
GUL = "FFCF0D"

# Blokk-taggene gir linjeskift, resten fjernes.
#
# <figure> tas ut MED innhold. Vaskeanvisningene i BWW er bygget som en rekke
# <figure> med et vaskesymbol som <img> og en <figcaption> under. Uten bildene
# blir bildetekstene staaende igjen som en loes liste ("Normal vask. Maks 40°",
# "Skal ikke strykes", ...) rett etter den samme informasjonen i lopende tekst.
#
# Merk at container og void-element MAA behandles hver for seg: en felles
# regex med alternativet `/?>` matcher `>` i selve aapningstaggen, saa bare
# taggen forsvinner og innholdet blir staaende. Det var nettopp feilen her.
_BLOKK = re.compile(r"</(p|div|li|tr|h[1-6])\s*>|<br\s*/?>", re.I)
_MED_INNHOLD = re.compile(r"<(figure|table|script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_VOID = re.compile(r"<(img|hr|input)\b[^>]*/?>", re.I)
_TAGG = re.compile(r"<[^>]+>")
_TOMME_LINJER = re.compile(r"\n{3,}")


def vask(rå: str) -> str:
    """HTML fra Magento til lesbar tekst. Tom streng hvis ingenting blir igjen."""
    if not rå:
        return ""
    t = _MED_INNHOLD.sub(" ", rå)
    t = _VOID.sub(" ", t)
    t = _BLOKK.sub("\n", t)
    t = _TAGG.sub("", t)
    t = htmllib.unescape(t)
    t = t.replace("\xa0", " ")
    t = "\n".join(linje.strip() for linje in t.split("\n"))
    t = _TOMME_LINJER.sub("\n\n", t)
    return t.strip()


def _avsnitt(doc, tekst: str, storrelse: float = 9.5, farge=MORK, fet: bool = False,
             sperring: int = 0, etter: float = 4, for_: float = 0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(etter)
    p.paragraph_format.space_before = Pt(for_)
    r = p.add_run(tekst)
    r.font.size = Pt(storrelse)
    r.font.color.rgb = farge
    r.bold = fet
    if sperring:
        e = OxmlElement("w:spacing")
        e.set(qn("w:val"), str(sperring))
        r._element.get_or_add_rPr().append(e)
    return p


def _strek(p, farge=HARLIN, tykkelse=4):
    """Harlinje under et avsnitt, i stedet for en ramme rundt noe."""
    b = OxmlElement("w:pBdr")
    kant = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", str(tykkelse)), ("space", "6"), ("color", farge)):
        kant.set(qn("w:" + k), v)
    b.append(kant)
    p._p.get_or_add_pPr().append(b)


def _etikett(doc, tekst: str):
    """Seksjonsetikett: sma graa kapitéler, som kolonneoverskriftene i malen."""
    return _avsnitt(doc, tekst.upper(), storrelse=8, farge=GRA, fet=True,
                    sperring=30, for_=10, etter=3)


def _bolk(doc, overskrift: str, tekst: str):
    """Etikett + tekst, men bare hvis det finnes tekst."""
    if not tekst:
        return
    _etikett(doc, overskrift)
    for blokk in tekst.split("\n\n"):
        if blokk.strip():
            _avsnitt(doc, blokk.strip(), storrelse=9.5)


def legg_til(doc, linjer: List[Dict], fakta: Dict[int, Dict[str, str]], kr) -> int:
    """
    Skriver ett datablad per vare bakerst i `doc` (et python-docx Document).

    `linjer` er de frosne tilbudslinjene, `fakta` kommer fra katalog.dataark().
    `kr` er tallformateringen fra dokument.py, slik at prisene ser like ut som
    i pristabellen. Returnerer antall ark som ble skrevet.

    Samme vare to ganger i tilbudet gir ett ark, ikke to.

    Varer utenfor nettbutikken (uten `produkt_id`) får ikke ark: vi har ingen
    beskrivelse eller vaskeanvisning for dem, bare det som står i pristabellen.
    """
    sett = set()
    antall = 0

    for l in linjer:
        mid = l.get("produkt_id")
        if not mid or mid in sett:
            continue
        sett.add(mid)

        # page_break_before, IKKE et eget avsnitt med et sideskift i: et slikt
        # avsnitt flyter selv over til neste side naar den forrige er full, og
        # da blir det staaende en helt blank side foran arket.
        forste = _avsnitt(doc, "PRODUKTDATABLAD", storrelse=8, farge=GRA, fet=True,
                          sperring=40, etter=2)
        forste.paragraph_format.page_break_before = True
        navn = _avsnitt(doc, l["navn"], storrelse=20, fet=True, sperring=10, etter=3)
        _strek(navn, farge=GUL, tykkelse=12)
        _avsnitt(doc, f"Varenr. {l['sku']}", storrelse=9, farge=GRA, etter=10)

        sti = bilders(l.get("bilde"))
        if sti:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(10)
            p.add_run().add_picture(sti, width=BILDEBREDDE)

        netto = Decimal(str(l["nettopris"]))
        sats = Decimal(str(l.get("mva_sats") or 0))
        inkl = netto + (netto * sats / Decimal("100"))
        _etikett(doc, "Din pris i dette tilbudet")
        _avsnitt(doc, f"{kr(netto)} kr eks. mva", storrelse=14, fet=True, etter=1)
        _avsnitt(doc, f"{kr(inkl)} kr inkl. mva", storrelse=9, farge=GRA, etter=4)

        f = fakta.get(mid, {})
        _bolk(doc, "Beskrivelse", vask(f.get("description", "")))
        _bolk(doc, "Teknisk informasjon", vask(f.get("teknisk_beskrivelse", "")))
        _bolk(doc, "Vaskeanvisning", vask(f.get("vaskeanvisning", "")))
        if f.get("ean"):
            _avsnitt(doc, f"EAN {f['ean']}", storrelse=8, farge=GRA, for_=10)

        antall += 1

    return antall


def bilders(sti):
    """Bildet i full databladstorrelse. Feiler den, droppes bildet - ikke arket."""
    try:
        return bilder.miniatyr(sti, bredde=650)
    except Exception:
        return None
