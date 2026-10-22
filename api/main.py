"""
tilbud.betterworkwear.no - tilbudsgenerator for selgere.

Innlogging er passordlos engangskode pa e-post, som i puls. Forskjellen er at
koden lagres i basen med utlopstid, ikke i minnet - en omstart av tjenesten
logger da ikke ut noen midt i innloggingen.

Tilgang krever en aktiv bruker i `users`. Det finnes ingen selvregistrering:
brukere opprettes av en admin.
"""
import logging
import os
import secrets
import string
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature
from starlette.concurrency import run_in_threadpool

from api import bilder, eksport, ekstern, katalog, epost, tjeneste
from api.db import tilbud_db
from api.dokument import kr
from api.prising import PrisFeil
from api.tjeneste import BrukerFeil, TilgangFeil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tilbud")

app = FastAPI(title="Tilbud — Better WorkWear", docs_url=None, redoc_url=None, openapi_url=None)

NETTSTED = os.environ.get("TILBUD_URL", "https://tilbud.betterworkwear.no").rstrip("/")


@app.middleware("http")
async def samme_opphav(request: Request, call_next):
    """CSRF-vern (03.10.2026): alle POST må komme fra tilbud-siden selv. SameSite=Lax alene slapp gjennom
    andre *.betterworkwear.no-sider, og en slik side kunne opprette en admin-bruker via POST /brukere."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        opphav = request.headers.get("origin")
        if not opphav:
            ref = request.headers.get("referer", "")
            opphav = "/".join(ref.split("/")[:3]) if ref else ""
        if opphav != NETTSTED:
            return JSONResponse({"feil": "Forespørselen kom ikke fra tilbud-siden."}, status_code=403)
    svar = await call_next(request)
    svar.headers.setdefault("X-Content-Type-Options", "nosniff")
    svar.headers.setdefault("X-Frame-Options", "DENY")
    svar.headers.setdefault("Referrer-Policy", "same-origin")
    svar.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return svar
templates = Jinja2Templates(directory="ui/templates")
# NB: nyere Starlette krever request som FORSTE argument til TemplateResponse.
# Gammel signatur (navn, context) gir "TypeError: unhashable type: dict",
# fordi context-dikten havner der malnavnet skulle vaert.
templates.env.filters["kr"] = kr
# Prosent uten unodige desimaler: 10 og ikke 10,00. Kan ikke bruke kr-filteret,
# som alltid gir to desimaler.
templates.env.filters["prosent"] = lambda v: (
    f"{Decimal(str(v)):.2f}".rstrip("0").rstrip(".") if v is not None else "")

_signer = URLSafeTimedSerializer(os.environ["TILBUD_SECRET_KEY"], salt="okt")
OKT_SEKUNDER = 60 * 60 * 12   # håndheves også på serveren, ikke bare som cookie-levetid i nettleseren
COOKIE = "tilbud_okt"
KODE_TEGN = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # uten I/O/0/1 - leses hoyt over telefon
KODE_MINUTTER = 10


# ---------------------------------------------------------------- okt/bruker

def _sett_okt(svar, epost_adr: str):
    svar.set_cookie(COOKIE, _signer.dumps({"e": epost_adr}),
                    httponly=True, secure=True, samesite="lax", max_age=OKT_SEKUNDER)


def innlogget(request: Request) -> Optional[dict]:
    """Leser okta og henter brukeren pa nytt fra basen ved hver forespørsel.

    Med vilje ikke cachet i cookien: deaktiveres en bruker eller endres rollen,
    skal det virke umiddelbart, ikke etter at okta utloper."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _signer.loads(raw, max_age=OKT_SEKUNDER)
    except BadSignature:
        return None
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE email=%s AND is_active=1", (data["e"],))
        return cur.fetchone()
    finally:
        conn.close()


def _krev(request: Request) -> dict:
    bruker = innlogget(request)
    if not bruker:
        raise HTTPException(status_code=401, detail="Ikke innlogget")
    return bruker


# ---------------------------------------------------------------- innlogging

@app.get("/login", response_class=HTMLResponse)
async def login_side(request: Request, feil: str = "", epost_adr: str = ""):
    if innlogget(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html",
        {"request": request, "feil": feil, "epost": epost_adr, "steg": "epost"})


@app.post("/login/kode", response_class=HTMLResponse)
async def send_kode(request: Request, epost_adr: str = Form(..., alias="epost")):
    epost_adr = epost_adr.strip().lower()
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE email=%s AND is_active=1", (epost_adr,))
        bruker = cur.fetchone()
        # Samme svar uansett om brukeren finnes - ellers kan man kartlegge ansatte.
        if bruker:
            # Ugyldiggjor alle tidligere ubrukte koder for denne adressen.
            # Uten dette kan flere koder vaere gyldige samtidig: ber man om en
            # ny fordi den forste ikke kom fram, ligger det plutselig to-tre
            # fungerende koder i innboksen, og man taster like gjerne feil.
            # Bare den nyeste skal virke.
            cur.execute(
                "UPDATE login_codes SET used_at=NOW() WHERE email=%s AND used_at IS NULL",
                (epost_adr,))
            kode = (''.join(secrets.choice(KODE_TEGN) for _ in range(4)) + "-" +
                    ''.join(secrets.choice(KODE_TEGN) for _ in range(4)))
            cur.execute(
                "INSERT INTO login_codes (email,code,expires_at) VALUES (%s,%s,%s)",
                (epost_adr, kode, datetime.now() + timedelta(minutes=KODE_MINUTTER)))
            if not epost.send_innloggingskode(epost_adr, kode):
                logger.warning("Kunne ikke sende kode til %s (Mailgun ikke satt opp?)", epost_adr)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "login.html",
        {"request": request, "epost": epost_adr, "steg": "kode", "feil": ""})


@app.post("/login/verifiser")
async def verifiser(request: Request, epost_adr: str = Form(..., alias="epost"),
                    kode: str = Form(...)):
    epost_adr = epost_adr.strip().lower()
    kode = kode.strip().upper()
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id FROM login_codes
                WHERE email=%s AND code=%s AND used_at IS NULL AND expires_at > NOW()
                ORDER BY id DESC LIMIT 1""", (epost_adr, kode))
        rad = cur.fetchone()
        if not rad:
            return templates.TemplateResponse(request, "login.html",
                {"request": request, "epost": epost_adr, "steg": "kode",
                 "feil": "Ugyldig eller utløpt kode. Bruk den nyeste e-posten — ber du om en ny kode, slutter de gamle å virke."}, status_code=400)
        cur.execute("UPDATE login_codes SET used_at=NOW() WHERE id=%s", (rad["id"],))
    finally:
        conn.close()
    svar = RedirectResponse("/", status_code=303)
    _sett_okt(svar, epost_adr)
    return svar


@app.get("/logout")
async def logout():
    svar = RedirectResponse("/login", status_code=303)
    svar.delete_cookie(COOKIE)
    return svar


# ---------------------------------------------------------------- tilbud

@app.get("/", response_class=HTMLResponse)
async def oversikt(request: Request):
    bruker = innlogget(request)
    if not bruker:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "liste.html", {
        "request": request, "bruker": bruker,
        "tilbud": tjeneste.synlige_tilbud(bruker),
        "ser_alle": bruker["rolle"] in tjeneste.SER_ALLE,
    })


@app.get("/nytt", response_class=HTMLResponse)
async def nytt_skjema(request: Request):
    bruker = innlogget(request)
    if not bruker:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "nytt.html", {
        "request": request, "bruker": bruker, "mva_sats": katalog.MVA_SATS})


@app.get("/api/sok")
async def api_sok(request: Request, q: str = ""):
    _krev(request)
    return JSONResponse([{
        "produkt_id": p["produkt_id"], "sku": p["sku"], "navn": p["navn"],
        "type": p["type"],
        "listepris": str(p["listepris"]), "kampanjepris": str(p["kampanjepris"]),
        "listepris_vis": kr(p["listepris"]), "kampanjepris_vis": kr(p["kampanjepris"]),
        "prisvariasjon": p["prisvariasjon"],
        # Satsen ma vaere med: uten den regnet nettleseren med en hardkodet
        # 25 %, og et produkt i en annen tax class ville vist feil inkl.-pris
        # paa skjermen. Serveren regnet riktig uansett - men skjermen loy.
        "mva_sats": str(p["mva_sats"]),
        # Nettleseren far en vanlig URL mot butikken - se api/bilder.py.
        "bilde_url": bilder.bilde_url(p["bilde"]),
    } for p in katalog.sok(q)])


@app.post("/api/hent-url")
async def api_hent_url(request: Request):
    """Forslag til SKU, navn og pris fra en produktside utenfor nettbutikken.

    POST og ikke GET, slik at samme-opphav-sjekken over gjelder: en annen side
    skal ikke kunne få tjenesten til å hente adresser i selgerens navn."""
    _krev(request)
    data = await request.json()
    url = str(data.get("url") or "")
    try:
        # Blokkerende nettverkskall - kjøres i en tråd så resten av tjenesten ikke venter.
        return JSONResponse(await run_in_threadpool(ekstern.hent_produkt, url))
    except ekstern.HentFeil as e:
        return JSONResponse({"feil": str(e)}, status_code=400)
    except Exception:
        logger.exception("Uventet feil ved henting av %s", url)
        return JSONResponse({"feil": "Klarte ikke lese siden. Fyll inn feltene selv."},
                            status_code=400)


def _des(v) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", ".").strip())
    except InvalidOperation:
        raise PrisFeil(f"«{v}» er ikke et gyldig tall.")


def _les_skjema(data: dict) -> dict:
    """Skjemaets JSON (nytt eller redigert tilbud) til argumenter for tjeneste-laget."""
    linjer = []
    for v in data.get("linjer", []):
        rabatt = _des(v.get("rabatt_prosent"))
        fast = _des(v.get("fastpris"))
        # Enten/eller. Skjemaet hindrer det, men skjemaet er ikke sannhetskilde.
        if rabatt is not None and fast is not None:
            raise PrisFeil("Sett enten rabatt eller fastpris på en linje, ikke begge.")
        manuell = None
        if v.get("manuell"):
            m = v["manuell"]
            manuell = {"sku": str(m.get("sku") or ""), "navn": str(m.get("navn") or ""),
                       "pris": _des(m.get("pris")), "inkl_mva": bool(m.get("inkl_mva")),
                       "kilde_url": str(m.get("kilde_url") or ""),
                       "kilde_bilde_url": str(m.get("kilde_bilde_url") or ""),
                       "kilde_beskrivelse": str(m.get("kilde_beskrivelse") or "")}
        linjer.append({
            # Bare ved redigering: en linje som allerede står i tilbudet.
            "linje_id": v.get("linje_id"),
            "produkt_id": v.get("produkt_id"),
            "manuell": manuell,
            "antall": v.get("antall", 1),
            "rabatt_type": ("prosent" if rabatt is not None
                            else "fastpris" if fast is not None else "ingen"),
            "rabatt_prosent": rabatt,
            "fastpris": fast,
        })
    return {
        "kunde": {"firma": data.get("kunde_firma",""), "kontakt": data.get("kunde_kontakt",""),
                  "epost": data.get("kunde_epost",""), "telefon": data.get("kunde_telefon","")},
        "varelinjer": linjer,
        "notat": data.get("notat",""),
    }


@app.post("/nytt")
async def opprett(request: Request):
    bruker = _krev(request)
    data = await request.json()
    try:
        t = tjeneste.opprett_tilbud(bruker, **_les_skjema(data))
    except PrisFeil as e:
        return JSONResponse({"feil": str(e)}, status_code=400)

    sendt = epost.send_tilbud(bruker["email"], t["tilbudsnr"],
                              t["kunde_firma"], t["docx_path"])
    return JSONResponse({"id": t["id"], "tilbudsnr": t["tilbudsnr"], "epost_sendt": sendt})


def _skjemalinje(l: dict) -> dict:
    """En lagret linje i den formen skjemaet (nytt.html) bruker for linjene sine."""
    rabatt = "" if l["rabatt_prosent"] is None else f'{l["rabatt_prosent"]:.2f}'.rstrip("0").rstrip(".")
    return {
        "linje_id": l["id"], "produkt_id": l["produkt_id"],
        "manuell": not l["produkt_id"] and not l["magento_id"],
        "sku": l["sku"], "navn": l["navn"], "antall": l["antall"],
        "listepris": str(l["listepris"]), "kampanjepris": str(l["kampanjepris"]),
        "listepris_vis": kr(l["listepris"]), "mva_sats": str(l["mva_sats"]),
        "bilde_url": bilder.bilde_url(l["bilde"]) or l["kilde_bilde_url"] or "",
        "modus": l["rabatt_type"], "rabatt": rabatt,
        "fastpris": "" if l["fastpris"] is None else str(l["fastpris"]),
    }


@app.get("/tilbud/{tilbud_id}/rediger", response_class=HTMLResponse)
async def rediger_skjema(request: Request, tilbud_id: int):
    bruker = innlogget(request)
    if not bruker:
        return RedirectResponse("/login", status_code=303)
    t = tjeneste.hent_tilbud(bruker, tilbud_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tilbudet finnes ikke")
    return templates.TemplateResponse(request, "nytt.html", {
        "request": request, "bruker": bruker, "mva_sats": katalog.MVA_SATS,
        "t": t, "linjer": [_skjemalinje(l) for l in t["linjer"]]})


@app.post("/tilbud/{tilbud_id}/rediger")
async def lagre_endring(request: Request, tilbud_id: int):
    bruker = _krev(request)
    data = await request.json()
    try:
        t = tjeneste.oppdater_tilbud(bruker, tilbud_id, **_les_skjema(data))
    except PrisFeil as e:
        return JSONResponse({"feil": str(e)}, status_code=400)
    if not t:
        raise HTTPException(status_code=404, detail="Tilbudet finnes ikke")

    # Til den som redigerte - det er den som skal sende det videre nå.
    sendt = epost.send_tilbud(bruker["email"], t["tilbudsnr"],
                              t["kunde_firma"], t["docx_path"])
    return JSONResponse({"id": t["id"], "tilbudsnr": t["tilbudsnr"], "epost_sendt": sendt})


@app.get("/tilbud/{tilbud_id}", response_class=HTMLResponse)
async def detalj(request: Request, tilbud_id: int):
    bruker = innlogget(request)
    if not bruker:
        return RedirectResponse("/login", status_code=303)
    t = tjeneste.hent_tilbud(bruker, tilbud_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tilbudet finnes ikke")
    return templates.TemplateResponse(request, "detalj.html",
        {"request": request, "bruker": bruker, "t": t,
         "kan_eksportere": eksport.har_tilgang(bruker)})


@app.get("/tilbud/{tilbud_id}/dokument")
async def last_ned(request: Request, tilbud_id: int):
    bruker = _krev(request)
    t = tjeneste.hent_tilbud(bruker, tilbud_id)
    if not t or not t["docx_path"] or not os.path.exists(t["docx_path"]):
        raise HTTPException(status_code=404, detail="Dokumentet finnes ikke")
    return FileResponse(t["docx_path"], filename=f"Tilbud-{t['tilbudsnr']}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/tilbud/{tilbud_id}/xlsx")
async def last_ned_xlsx(request: Request, tilbud_id: int):
    """Excel-eksport. Bare for EKSPORT_TILGANG - knappen skjules for andre, men det er HER det håndheves."""
    bruker = _krev(request)
    if not eksport.har_tilgang(bruker):
        raise HTTPException(status_code=403, detail="Ingen tilgang til eksport")
    t = tjeneste.hent_tilbud(bruker, tilbud_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tilbudet finnes ikke")
    return Response(
        await run_in_threadpool(eksport.lag_xlsx, t),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="Tilbud-{t["tilbudsnr"]}.xlsx"'})


@app.post("/tilbud/{tilbud_id}/sendt")
async def sett_sendt(request: Request, tilbud_id: int):
    bruker = _krev(request)
    if not tjeneste.marker_sendt(bruker, tilbud_id):
        raise HTTPException(status_code=404, detail="Tilbudet finnes ikke")
    return RedirectResponse(f"/tilbud/{tilbud_id}", status_code=303)


@app.get("/frisk")
async def frisk():
    return {"status": "ok"}


# ---------------------------------------------------------------- brukeradmin

@app.get("/brukere", response_class=HTMLResponse)
async def brukere(request: Request, feil: str = "", ok: str = ""):
    bruker = innlogget(request)
    if not bruker:
        return RedirectResponse("/login", status_code=303)
    try:
        liste = tjeneste.alle_brukere(bruker)
    except TilgangFeil:
        raise HTTPException(status_code=403, detail="Krever administrator")
    return templates.TemplateResponse(request, "brukere.html", {
        "request": request, "bruker": bruker, "brukere": liste,
        "roller": tjeneste.ROLLER, "feil": feil, "ok": ok,
    })


@app.post("/brukere")
async def ny_bruker(request: Request, epost_adr: str = Form(..., alias="epost"),
                    navn: str = Form(""), telefon: str = Form(""),
                    rolle: str = Form("selger")):
    bruker = _krev(request)
    try:
        ny = tjeneste.opprett_bruker(bruker, epost_adr, navn, telefon, rolle)
    except TilgangFeil:
        raise HTTPException(status_code=403, detail="Krever administrator")
    except BrukerFeil as e:
        return RedirectResponse(f"/brukere?feil={e}", status_code=303)
    return RedirectResponse(
        f"/brukere?ok={ny['email']} er lagt til som {ny['rolle']}", status_code=303)


@app.post("/brukere/{bruker_id}")
async def endre_bruker(request: Request, bruker_id: int, navn: str = Form(""),
                       telefon: str = Form(""), rolle: str = Form("selger"),
                       aktiv: str = Form("")):
    bruker = _krev(request)
    try:
        tjeneste.oppdater_bruker(bruker, bruker_id, navn, telefon, rolle, aktiv == "1")
    except TilgangFeil:
        raise HTTPException(status_code=403, detail="Krever administrator")
    except BrukerFeil as e:
        return RedirectResponse(f"/brukere?feil={e}", status_code=303)
    return RedirectResponse("/brukere?ok=Endringen er lagret", status_code=303)
