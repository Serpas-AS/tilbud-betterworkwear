"""
«Du sparer»-linja i sumtabellen (22.10.2026).

Kjøres fra prosjektroten, én gang: venv/bin/python maler/sparelinje.py
Som omform-mal.py er dette dokumentasjon av en endring i malen, ikke en del
av tjenesten. Sikkerhetskopi før endringen: maler/tilbud-mal.docx.bak-sparer-2026-10-22.

Legger inn to rader øverst i sumtabellen, pakket i {%tr if spart %} slik at de
bare står der når kunden faktisk får noe under veil. pris:

    Veil. pris eks. mva     3 967,20 kr
    Du sparer              −1 691,60 kr (43 %)
    Sum eks. mva            2 275,60 kr   <- radene som var der fra før
    ...

«Du sparer» er mørk og fet, resten grått som før. Ikke gult: profilen bruker
gult én gang i dokumentet, under logoen.
"""
import copy
import sys

import docx
from docx.oxml.ns import qn

MAL = "maler/tilbud-mal.docx"

d = docx.Document(MAL)
sum_t = d.tables[2]
if "spart" in sum_t._tbl.xml:
    sys.exit("Malen har allerede sparelinja.")

forste = sum_t.rows[0]._tr   # «Sum eks. mva» - kopieres for å arve bredder og stil


def rad(venstre: str, hoyre: str, mork: bool = False):
    tr = copy.deepcopy(forste)
    for tc, tekst in zip(tr.findall(qn("w:tc")), (venstre, hoyre)):
        r = tc.find(qn("w:p")).find(qn("w:r"))
        r.find(qn("w:t")).text = tekst
        if mork:
            rpr = r.find(qn("w:rPr"))
            rpr.find(qn("w:b")).set(qn("w:val"), "1")
            rpr.find(qn("w:color")).set(qn("w:val"), "12100C")
    return tr


# docxtpl fjerner hele raden som har {%tr ... %}, så de to markørradene blir borte.
for tr in [
    rad("{%tr if spart %}", ""),
    rad("Veil. pris eks. mva", "{{ veil_eks_mva }} kr"),
    rad("Du sparer", "−{{ spart }} kr ({{ spart_prosent }} %)", mork=True),
    rad("{%tr endif %}", ""),
]:
    forste.addprevious(tr)

d.save(MAL)
print("Sparelinja lagt inn.")
