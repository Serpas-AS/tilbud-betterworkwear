"""
E-postutsending via Mailgun - samme konto og domene som nettbutikken.

Bruker BWWs eget avsenderdomene, ikke wrks.no. Domenet er allerede
SPF/DKIM-verifisert for betterworkwear.no, sa e-posten havner ikke i
soppelpost, og skal tilbudet en dag ga rett til kunde, star det riktig
avsender pa det.

HTTP-API-et brukes når MAILGUN_API_KEY er satt, som i ny.betterworkwear.no.
SMTP-reserven: smtp.eu.mailgun.org:465 (SSL), bruker postmaster@betterworkwear.no

Alle verdier leses fra miljoet. Ingen hemmeligheter i kode - puls.wrks.no har
Mailgun-nokkelen hardkodet i api/routes/auth.py, og da folger den med hver
kopi av kodebasen.

Er SMTP_PASSWORD tom, sendes ingenting og funksjonene returnerer False.
Tjenesten skal fortsatt virke: dokumentet er generert og kan lastes ned i
UI-et. En manglende e-postnokkel skal ikke miste et tilbud selgeren har laget.
"""
import html as htmllib
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import requests

logger = logging.getLogger("tilbud.epost")

TIMEOUT = 20   # sekunder - aldri et nettverkskall uten timeout i en request-sti
DOCX_TYPE = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")


def _kanal() -> str:
    """
    Mailgun har TO ulike legitimasjoner, og de er ikke utbyttbare:
      SMTP_PASSWORD    - for smtp.eu.mailgun.org, knyttet til postmaster@
      MAILGUN_API_KEY  - for HTTP-API-et

    Begge sender fra samme verifiserte domene, sa avsenderen blir lik.
    Er begge satt, vinner API-et: ett HTTP-kall er raskere enn en
    SMTP-handtrykk med TLS, og feiler tydeligere.
    """
    if os.environ.get("MAILGUN_API_KEY"):
        return "api"
    if os.environ.get("SMTP_PASSWORD"):
        return "smtp"
    return "ingen"


def _konfigurert() -> bool:
    return _kanal() != "ingen"


def _send_api(til: str, emne: str, html: str, vedlegg=None) -> bool:
    files = []
    if vedlegg:
        sti, filnavn = vedlegg
        files = [("attachment", (filnavn, open(sti, "rb"),
                                 f"{DOCX_TYPE[0]}/{DOCX_TYPE[1]}"))]
    try:
        r = requests.post(
            f"https://api.eu.mailgun.net/v3/{os.environ['MAILGUN_DOMAIN']}/messages",
            auth=("api", os.environ["MAILGUN_API_KEY"]),
            data={"from": os.environ["SMTP_FROM"], "to": til,
                  "subject": emne, "html": html},
            files=files,
            timeout=(5, 20),   # connect, read - aldri uten timeout
        )
        if r.status_code >= 300:
            logger.error("Mailgun-API svarte %s for %s: %s",
                         r.status_code, til, r.text[:200])
            return False
        return True
    except requests.RequestException as e:
        logger.error("Mailgun-API feilet for %s: %s", til, e)
        return False
    finally:
        for _, (_, fh, _) in files:
            fh.close()


def _send(til: str, emne: str, html: str, vedlegg=None) -> bool:
    kanal = _kanal()
    if kanal == "ingen":
        logger.warning(
            "Verken MAILGUN_API_KEY eller SMTP_PASSWORD er satt — "
            "hopper over e-post til %s", til)
        return False
    if kanal == "api":
        return _send_api(til, emne, html, vedlegg)

    msg = EmailMessage()
    navn, adresse = parseaddr(os.environ["SMTP_FROM"])
    msg["From"] = formataddr((navn, adresse))
    msg["To"] = til
    msg["Subject"] = emne
    msg.set_content("Denne meldingen krever en e-postleser som viser HTML.")
    msg.add_alternative(html, subtype="html")

    if vedlegg:
        sti, filnavn = vedlegg
        with open(sti, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=DOCX_TYPE[0],
                               subtype=DOCX_TYPE[1], filename=filnavn)

    try:
        with smtplib.SMTP_SSL(
            os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]),
            context=ssl.create_default_context(), timeout=TIMEOUT,
        ) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        # Feiler stille mot brukeren, men hoyt i loggen. Et tilbud skal ikke
        # ga tapt fordi e-posttjenesten er nede - dokumentet ligger lagret.
        logger.error("SMTP-utsending til %s feilet: %s", til, e)
        return False


def send_innloggingskode(til: str, kode: str) -> bool:
    return _send(til, f"{kode} — innloggingskode for Tilbud", f"""
      <div style="font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;text-align:center">
        <div style="font-size:20px;font-weight:700;margin-bottom:8px">Better WorkWear — Tilbud</div>
        <p style="color:#666;margin-bottom:28px">Din innloggingskode:</p>
        <div style="font-size:34px;font-weight:700;letter-spacing:5px;background:#f4f5f7;
                    padding:22px;border-radius:12px;margin-bottom:20px">{kode}</div>
        <p style="color:#999;font-size:13px">Gyldig i 10 minutter. Del den ikke med andre.</p>
      </div>""")


def send_tilbud(til: str, tilbudsnr: str, kunde: str, docx_sti: str, lenke: str = "") -> bool:
    # Kundenavnet er fritekst fra skjemaet - escapes før det settes inn i HTML-en.
    k = htmllib.escape(kunde or "")
    knapp = (f'''<p><a href="{htmllib.escape(lenke)}" style="display:inline-block;background:#12100c;
           color:#fff;padding:10px 18px;border-radius:999px;text-decoration:none;font-weight:600">
           Åpne tilbudet</a></p>''' if lenke else "")
    return _send(til, f"Tilbud {tilbudsnr} — {kunde or 'uten kundenavn'}", f"""
      <div style="font-family:system-ui,sans-serif;max-width:520px;margin:32px auto">
        <p>Tilbud <strong>{tilbudsnr}</strong>{f' til {k}' if k else ''} er klart.</p>
        <p>Dokumentet ligger vedlagt som Word-fil. Gjør de justeringene du vil,
           og send det videre til kunden.</p>
        {knapp}
        <p style="color:#888;font-size:13px">Når det har gått ut: marker tilbudet som sendt,
           og senere som vunnet eller tapt. Da ser ledelsen hva som faktisk ble salg.</p>
      </div>""", vedlegg=(docx_sti, f"Tilbud-{tilbudsnr}.docx"))
