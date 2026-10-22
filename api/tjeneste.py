"""
Forretningslogikk for tilbud: oppretting, redigering, henting, status (sendt/vunnet/tapt).

Rollemodellen:
  selger  - ser og lager sine egne tilbud
  ledelse - ser alle tilbud, lager ogsa egne
  admin   - som ledelse, pluss brukeradministrasjon

Tilgangen avgjores i `synlige_tilbud()` og `hent_tilbud()`, ikke i
rutene - da kan ikke en ny rute glemme sjekken.
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from api.db import tilbud_db
from api import bilder, katalog, dokument
from api.prising import beregn_linje, summer, PrisFeil

SER_ALLE = ("ledelse", "admin")


def _neste_tilbudsnr(cur) -> str:
    aar = datetime.now().year
    cur.execute(
        "SELECT tilbudsnr FROM tilbud WHERE tilbudsnr LIKE %s ORDER BY id DESC LIMIT 1",
        (f"T-{aar}-%",),
    )
    rad = cur.fetchone()
    neste = int(rad["tilbudsnr"].split("-")[-1]) + 1 if rad else 1
    return f"T-{aar}-{neste:04d}"


def manuell_produkt(m: Dict) -> Dict:
    """
    En vare som ikke er i nettbutikken, i samme form som katalog.hent().

    m: {sku, navn, pris (Decimal), inkl_mva (bool), kilde_url}. Prisen er det
    selgeren godkjente i skjemaet - eventuelt hentet fra leverandørens side
    av api/ekstern.py, men rettet av selgeren. Den regnes om til eks. mva her,
    med samme sats og avrunding som katalogen, og er både veil. pris og
    utgangspunkt for rabatt (det finnes ingen kampanjepris).
    """
    navn = (m.get("navn") or "").strip()
    sku = (m.get("sku") or "").strip()
    kilde = (m.get("kilde_url") or "").strip()
    pris = m.get("pris")
    if not navn:
        raise PrisFeil("En vare utenfor nettbutikken må ha et navn.")
    if len(navn) > 255 or len(sku) > 64:
        raise PrisFeil(f"Navnet eller varenummeret til «{navn[:40]}» er for langt.")
    if pris is None or pris <= 0:
        raise PrisFeil(f"«{navn}» mangler pris.")
    # Lenkene vises som klikkbare / lastes ned - bare http(s), aldri javascript: o.l.
    if kilde and (not kilde.lower().startswith(("https://", "http://")) or len(kilde) > 500):
        kilde = ""
    beskrivelse = (m.get("kilde_beskrivelse") or "").strip()
    if len(beskrivelse) > 5000:
        raise PrisFeil(f"Beskrivelsen til «{navn[:40]}» er for lang (maks 5000 tegn).")
    bilde = (m.get("kilde_bilde_url") or "").strip()
    if bilde and (not bilde.lower().startswith(("https://", "http://")) or len(bilde) > 1000):
        bilde = ""
    eks = pris if not m.get("inkl_mva") else katalog._eks_mva(pris, katalog.MVA_SATS)
    eks = katalog._q2(eks)
    return {
        "produkt_id": None, "sku": sku, "navn": navn, "bilde": None,
        "listepris": eks, "kampanjepris": eks, "mva_sats": katalog.MVA_SATS,
        "kilde_url": kilde or None, "kilde_bilde_url": bilde or None,
        "kilde_beskrivelse": beskrivelse or None,
    }


def frosset_produkt(l: Dict) -> Dict:
    """
    En lagret tilbudslinje i samme form som katalog.hent(), for redigering.

    Linjer som står igjen når et tilbud redigeres, beholder prisene, bildet og
    teksten de ble frosset med - ellers ville en endring av antall på én linje
    stille og rolig flyttet alle de andre linjene til dagens katalogpris.
    """
    return {
        "produkt_id": l["produkt_id"], "magento_id": l.get("magento_id"),
        "sku": l["sku"], "navn": l["navn"], "bilde": l.get("bilde"),
        "listepris": l["listepris"], "kampanjepris": l["kampanjepris"],
        "mva_sats": l["mva_sats"], "kilde_url": l.get("kilde_url"),
        "kilde_bilde_url": l.get("kilde_bilde_url"),
        "kilde_beskrivelse": l.get("kilde_beskrivelse"),
    }


def _beregn(varelinjer: List[Dict], frosne: Optional[Dict[int, Dict]] = None) -> List[Dict]:
    """
    varelinjer: [{linje_id | produkt_id | manuell, antall, rabatt_type, rabatt_prosent, fastpris}, ...]

    Prisene slas opp fra katalogen HER, ikke i nettleseren - det som sendes inn
    fra skjemaet er kun produktvalg og rabatt. Deretter fryses resultatet.
    Unntakene er varer utenfor nettbutikken (`manuell`), der selgeren selv
    oppgir prisen - se manuell_produkt(), og linjer som allerede finnes i
    tilbudet som redigeres (`linje_id`), som beholder sin frosne pris.
    """
    if not varelinjer:
        raise PrisFeil("Tilbudet må inneholde minst én vare.")

    beregnede = []
    for v in varelinjer:
        if v.get("linje_id") is not None:
            # Slås opp blant tilbudets egne linjer - en linje_id fra et annet
            # tilbud gir feil, ikke en kopi av noen andres priser.
            frossen = (frosne or {}).get(int(v["linje_id"]))
            if not frossen:
                raise PrisFeil("En av linjene finnes ikke lenger i tilbudet. Last siden på nytt.")
            produkt = frosset_produkt(frossen)
        elif v.get("manuell"):
            produkt = manuell_produkt(v["manuell"])
        else:
            produkt = katalog.hent(int(v["produkt_id"]))
            if not produkt:
                raise PrisFeil(f"Fant ikke produkt {v['produkt_id']} i katalogen.")
        beregnede.append(beregn_linje(
            produkt,
            antall=v.get("antall", 1),
            rabatt_type=v.get("rabatt_type", "ingen"),
            rabatt_prosent=v.get("rabatt_prosent"),
            fastpris=v.get("fastpris"),
        ))
        beregnede[-1]["magento_id"] = produkt.get("magento_id")
        beregnede[-1]["kilde_url"] = produkt.get("kilde_url")
        beregnede[-1]["kilde_bilde_url"] = produkt.get("kilde_bilde_url")
        beregnede[-1]["kilde_beskrivelse"] = produkt.get("kilde_beskrivelse")
        # Lastes ned og fryses nå, før dokumentet lages. Feiler det, beholdes
        # adressen likevel (Excel-eksporten bruker den); Word får bare ikke bilde.
        bilder.hent_ekstern(produkt.get("kilde_bilde_url"))
    return beregnede


def _skriv_linjer(cur, tilbud_id: int, beregnede: List[Dict]):
    for i, l in enumerate(beregnede):
        cur.execute(
            """INSERT INTO tilbud_linje
               (tilbud_id,sort_order,produkt_id,magento_id,sku,navn,bilde,antall,listepris,
                kampanjepris,rabatt_type,rabatt_prosent,fastpris,nettopris,
                linjesum,mva_sats,kilde_url,kilde_bilde_url,kilde_beskrivelse)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tilbud_id, i, l["produkt_id"], l.get("magento_id"), l["sku"], l["navn"],
             l.get("bilde"), l["antall"],
             l["listepris"], l["kampanjepris"], l["rabatt_type"],
             l["rabatt_prosent"], l["fastpris"], l["nettopris"],
             l["linjesum"], l["mva_sats"], l.get("kilde_url"), l.get("kilde_bilde_url"),
             l.get("kilde_beskrivelse")),
        )


def opprett_tilbud(bruker: Dict, kunde: Dict, varelinjer: List[Dict],
                   notat: str = "") -> Dict:
    """varelinjer: se _beregn()."""
    beregnede = _beregn(varelinjer)
    totaler = summer(beregnede)

    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        nr = _neste_tilbudsnr(cur)
        cur.execute(
            """INSERT INTO tilbud (tilbudsnr,user_id,kunde_firma,kunde_kontakt,
                                   kunde_epost,kunde_telefon,notat,
                                   sum_eks_mva,sum_mva,sum_inkl_mva)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (nr, bruker["id"], kunde.get("firma",""), kunde.get("kontakt",""),
             kunde.get("epost",""), kunde.get("telefon",""), notat,
             totaler["sum_eks_mva"], totaler["sum_mva"], totaler["sum_inkl_mva"]),
        )
        tilbud_id = cur.lastrowid
        _skriv_linjer(cur, tilbud_id, beregnede)

        cur.execute("SELECT * FROM tilbud WHERE id=%s", (tilbud_id,))
        rad = cur.fetchone()

        sti = dokument.generer(rad, beregnede, bruker)
        cur.execute("UPDATE tilbud SET docx_path=%s WHERE id=%s", (sti, tilbud_id))
        rad["docx_path"] = sti
        return rad
    finally:
        conn.close()


def oppdater_tilbud(bruker: Dict, tilbud_id: int, kunde: Dict, varelinjer: List[Dict],
                    notat: str = "") -> Optional[Dict]:
    """
    Redigerer et eksisterende tilbud og lager Word-dokumentet på nytt.

    Samme tilbudsnummer, dato og selger som før - dokumentet skal fortsatt
    vise den som eier tilbudet, også når ledelsen retter i det. Linjene
    byttes ut i én transaksjon, så et tilbud aldri står halvveis redigert.
    Returnerer None hvis tilbudet ikke finnes eller brukeren ikke har tilgang.
    """
    t = hent_tilbud(bruker, tilbud_id)
    if not t:
        return None
    beregnede = _beregn(varelinjer, {l["id"]: l for l in t["linjer"]})
    totaler = summer(beregnede)

    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        conn.start_transaction()
        cur.execute(
            """UPDATE tilbud SET kunde_firma=%s, kunde_kontakt=%s, kunde_epost=%s,
                                 kunde_telefon=%s, notat=%s,
                                 sum_eks_mva=%s, sum_mva=%s, sum_inkl_mva=%s
                WHERE id=%s""",
            (kunde.get("firma",""), kunde.get("kontakt",""), kunde.get("epost",""),
             kunde.get("telefon",""), notat,
             totaler["sum_eks_mva"], totaler["sum_mva"], totaler["sum_inkl_mva"], tilbud_id))
        cur.execute("DELETE FROM tilbud_linje WHERE tilbud_id=%s", (tilbud_id,))
        _skriv_linjer(cur, tilbud_id, beregnede)
        cur.execute("SELECT * FROM tilbud WHERE id=%s", (tilbud_id,))
        rad = cur.fetchone()
        cur.execute("SELECT * FROM users WHERE id=%s", (rad["user_id"],))
        selger = cur.fetchone()

        # Dokumentet lages før commit: feiler det, rulles endringen tilbake og
        # det gamle dokumentet passer fortsatt til det som står i basen.
        sti = dokument.generer(rad, beregnede, selger)
        cur.execute("UPDATE tilbud SET docx_path=%s WHERE id=%s", (sti, tilbud_id))
        conn.commit()
        rad["docx_path"] = sti
        return rad
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def synlige_tilbud(bruker: Dict, limit: int = 200) -> List[Dict]:
    """Selgere ser sine egne. Ledelse og admin ser alle."""
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        if bruker["rolle"] in SER_ALLE:
            cur.execute(
                """SELECT t.*, u.navn AS selger_navn, u.email AS selger_epost
                     FROM tilbud t JOIN users u ON u.id=t.user_id
                    ORDER BY t.created_at DESC LIMIT %s""", (limit,))
        else:
            cur.execute(
                """SELECT t.*, u.navn AS selger_navn, u.email AS selger_epost
                     FROM tilbud t JOIN users u ON u.id=t.user_id
                    WHERE t.user_id=%s ORDER BY t.created_at DESC LIMIT %s""",
                (bruker["id"], limit))
        return cur.fetchall()
    finally:
        conn.close()


def hent_tilbud(bruker: Dict, tilbud_id: int) -> Optional[Dict]:
    """Returnerer None hvis tilbudet ikke finnes ELLER brukeren ikke har tilgang."""
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT t.*, u.navn AS selger_navn, u.email AS selger_epost,
                      u.telefon AS selger_telefon
                 FROM tilbud t JOIN users u ON u.id=t.user_id
                WHERE t.id=%s""", (tilbud_id,))
        t = cur.fetchone()
        if not t:
            return None
        if bruker["rolle"] not in SER_ALLE and t["user_id"] != bruker["id"]:
            return None
        cur.execute(
            "SELECT * FROM tilbud_linje WHERE tilbud_id=%s ORDER BY sort_order",
            (tilbud_id,))
        t["linjer"] = cur.fetchall()
        return t
    finally:
        conn.close()


STATUSER = ("generert", "sendt", "vunnet", "tapt")


def sett_status(bruker: Dict, tilbud_id: int, status: str) -> bool:
    """
    «Sendt» er selvrapportert: systemet ser ikke e-posten selgeren sender fra
    sitt eget program. «Vunnet»/«tapt» (lagt til 22.10.2026) er utfallet, og
    forutsetter at tilbudet er sendt - sendt_at settes derfor også om den
    mangler. Tilbake til «sendt» er angre-knappen og nullstiller avgjørelsen.
    """
    if status not in STATUSER[1:]:
        raise ValueError(f"Ukjent status: {status}")
    if not hent_tilbud(bruker, tilbud_id):
        return False
    conn = tilbud_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE tilbud SET status=%s,
                      sendt_at=COALESCE(sendt_at, NOW()),
                      avgjort_at=IF(%s IN ('vunnet','tapt'), NOW(), NULL)
                WHERE id=%s""",
            (status, status, tilbud_id))
        return True
    finally:
        conn.close()


def oppsummering(tilbud: List[Dict]) -> Dict:
    """Antall og sum eks. mva per status, for toppen av oversikten."""
    ut = {s: {"antall": 0, "sum": Decimal("0")} for s in STATUSER}
    for t in tilbud:
        ut[t["status"]]["antall"] += 1
        ut[t["status"]]["sum"] += Decimal(str(t["sum_eks_mva"]))
    avgjort = ut["vunnet"]["antall"] + ut["tapt"]["antall"]
    ut["treff"] = round(100 * ut["vunnet"]["antall"] / avgjort) if avgjort else None
    return ut


# ------------------------------------------------------------ brukeradmin

ROLLER = ("selger", "ledelse", "admin")


class TilgangFeil(PermissionError):
    """Handlingen krever en rolle brukeren ikke har."""


class BrukerFeil(ValueError):
    """Ugyldig input eller en endring som ville last noen ute."""


def _krev_admin(bruker: Dict):
    if bruker["rolle"] != "admin":
        raise TilgangFeil("Bare administratorer kan endre brukere.")


def alle_brukere(bruker: Dict) -> List[Dict]:
    _krev_admin(bruker)
    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT u.*, COUNT(t.id) AS antall_tilbud
              FROM users u LEFT JOIN tilbud t ON t.user_id = u.id
             GROUP BY u.id
             ORDER BY u.is_active DESC, u.navn, u.email""")
        return cur.fetchall()
    finally:
        conn.close()


def _antall_aktive_admin(cur, utenom_id: Optional[int] = None) -> int:
    if utenom_id:
        cur.execute("SELECT COUNT(*) c FROM users WHERE rolle='admin' AND is_active=1 AND id<>%s",
                    (utenom_id,))
    else:
        cur.execute("SELECT COUNT(*) c FROM users WHERE rolle='admin' AND is_active=1")
    return cur.fetchone()["c"]


def opprett_bruker(bruker: Dict, epost: str, navn: str, telefon: str, rolle: str) -> Dict:
    _krev_admin(bruker)
    epost = (epost or "").strip().lower()
    if "@" not in epost or len(epost) < 5:
        raise BrukerFeil("Ugyldig e-postadresse.")
    if rolle not in ROLLER:
        raise BrukerFeil(f"Ukjent rolle: {rolle}")

    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE email=%s", (epost,))
        if cur.fetchone():
            raise BrukerFeil(f"{epost} finnes allerede.")
        cur.execute(
            "INSERT INTO users (email,navn,telefon,rolle) VALUES (%s,%s,%s,%s)",
            (epost, (navn or "").strip(), (telefon or "").strip(), rolle))
        cur.execute("SELECT * FROM users WHERE id=%s", (cur.lastrowid,))
        return cur.fetchone()
    finally:
        conn.close()


def oppdater_bruker(bruker: Dict, bruker_id: int, navn: str, telefon: str,
                    rolle: str, aktiv: bool) -> None:
    """
    Sperre mot utestenging: den SISTE aktive administratoren kan verken
    fratas admin-rollen eller deaktiveres. Uten den kan en feilklikk gjore
    at ingen lenger kan legge til brukere, og da ma man inn i databasen.
    """
    _krev_admin(bruker)
    if rolle not in ROLLER:
        raise BrukerFeil(f"Ukjent rolle: {rolle}")

    conn = tilbud_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE id=%s", (bruker_id,))
        mal = cur.fetchone()
        if not mal:
            raise BrukerFeil("Brukeren finnes ikke.")

        mister_admin = mal["rolle"] == "admin" and mal["is_active"] and (
            rolle != "admin" or not aktiv)
        if mister_admin and _antall_aktive_admin(cur, utenom_id=bruker_id) == 0:
            raise BrukerFeil(
                "Dette er den siste aktive administratoren. Gjør noen andre til "
                "administrator først, ellers kan ingen lenger legge til brukere.")

        cur.execute(
            "UPDATE users SET navn=%s, telefon=%s, rolle=%s, is_active=%s WHERE id=%s",
            ((navn or "").strip(), (telefon or "").strip(), rolle, 1 if aktiv else 0, bruker_id))
    finally:
        conn.close()
