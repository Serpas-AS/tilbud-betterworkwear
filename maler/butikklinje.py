"""
Butikken til selgeren under «Vår kontaktperson» (31.10.2026).

Kjøres fra prosjektroten, én gang: venv/bin/python maler/butikklinje.py
Som sparelinje.py er dette dokumentasjon av en endring i malen, ikke en del av
tjenesten. Sikkerhetskopi før endringen: maler/tilbud-mal.docx.bak-butikk-2026-10-31.

Variabelen står i samme tekstbit som telefonen og begynner med linjeskift
(samme grep som rabatt_tekst). Uten butikk er den tom, og da blir det ingen tom linje.
"""
import sys

import docx
from docx.oxml.ns import qn

MAL = "maler/tilbud-mal.docx"

d = docx.Document(MAL)
for t in d.element.body.iter(qn("w:t")):
    if t.text == "{{ selger_telefon }}":
        t.text = "{{ selger_telefon }}{{ butikk_tekst }}"
        break
else:
    sys.exit("Fant ikke telefonfeltet, eller malen har allerede butikken.")

d.save(MAL)
print("Butikken lagt inn.")
