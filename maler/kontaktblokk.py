"""
Kunde, kontaktperson og butikk i hver sin kolonne, og A4 i stedet for Letter (31.10.2026).

Kjøres fra prosjektroten, én gang: venv/bin/python maler/kontaktblokk.py [mal.docx]
Som sparelinje.py er dette dokumentasjon av en endring i malen, ikke en del av
tjenesten. Sikkerhetskopi før endringen: maler/tilbud-mal.docx.bak-kontakt-2026-10-31.

Navnet står fett øverst, resten under uten hull der et felt mangler: linjene
siles med `select` i malen. Butikken kommer som `butikk_tekst` med linjeskift
først (se butikklinje.py), derfor `strip()`.
Sidemargene settes så tekstbredden blir den samme (9746) på A4 - tabellene har faste bredder.
"""
import copy
import sys

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

MAL = sys.argv[1] if len(sys.argv) > 1 else "maler/tilbud-mal.docx"
BREDDE = 9746 // 3
MORK = '<w:rPr><w:color w:val="12100C"/><w:sz w:val="20"/></w:rPr>'
FET = '<w:rPr><w:b/><w:color w:val="12100C"/><w:sz w:val="20"/></w:rPr>'


def p(tekst: str, rpr: str):
    return parse_xml(f'<w:p {nsdecls("w")}><w:pPr><w:spacing w:after="0"/></w:pPr>'
                     f'<w:r>{rpr}<w:t xml:space="preserve">{tekst}</w:t></w:r></w:p>')


def fyll(tc, avsnitt):
    for gammel in tc.findall(qn("w:p")):
        tc.remove(gammel)
    for a in avsnitt:
        tc.append(a)


d = docx.Document(MAL)
t = d.tables[0]
if "butikk_tekst }}{" in t._tbl.xml or "BUTIKK" in t._tbl.xml:
    sys.exit("Malen har allerede kontaktblokken.")

# Tredje kolonne: kopi av den andre, så etikett og strek arves.
grid = t._tbl.tblGrid
grid.append(copy.deepcopy(grid.findall(qn("w:gridCol"))[1]))
for tr in t._tbl.findall(qn("w:tr")):
    tr.append(copy.deepcopy(tr.findall(qn("w:tc"))[1]))
for g in grid.findall(qn("w:gridCol")):
    g.set(qn("w:w"), str(BREDDE))
for tcw in t._tbl.iter(qn("w:tcW")):
    tcw.set(qn("w:w"), str(BREDDE))

etikett, _, butikk = t._tbl.findall(qn("w:tr"))[0].findall(qn("w:tc"))
next(butikk.iter(qn("w:t"))).text = "{{ 'BUTIKK' if butikk_tekst }}"

kunde, selger, butikk = t._tbl.findall(qn("w:tr"))[1].findall(qn("w:tc"))
fyll(kunde, [
    p("{%p if kunde_firma %}", MORK), p("{{ kunde_firma }}", FET), p("{%p endif %}", MORK),
    p("{{ [kunde_kontakt, kunde_epost, kunde_telefon]|select|join('\\n') }}", MORK),
])
fyll(selger, [
    p("{{ selger_navn }}", FET),
    p("{{ [selger_epost, selger_telefon]|select|join('\\n') }}", MORK),
])
# Avsnittet under {%p %}-blokken står alltid: en tabellcelle må ha minst ett.
fyll(butikk, [
    p("{%p if butikk_tekst %}", MORK), p("{{ butikk_tekst.strip().split('\\n')[0] }}", FET), p("{%p endif %}", MORK),
    p("{{ butikk_tekst.strip().split('\\n')[1:]|join('\\n') }}", MORK),
])

sek = d.element.body.find(qn("w:sectPr"))
sek.find(qn("w:pgSz")).set(qn("w:w"), "11906")
sek.find(qn("w:pgSz")).set(qn("w:h"), "16838")
for side in ("left", "right"):
    sek.find(qn("w:pgMar")).set(qn("w:" + side), "1080")

d.save(MAL)
print("Kontaktblokken lagt inn.")
