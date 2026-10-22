"""
Henter varenummer, navn og pris fra en produktside utenfor nettbutikken.

Lagt til 21.10.2026. Selgerne gir også tilbud på varer som ikke ligger i
`bww_web`. De limer inn lenken til produktsiden, tjenesten foreslår SKU, navn
og pris, og selgeren retter feltene før linja legges til. Det som hentes her
er et FORSLAG - det er tallene selgeren godkjenner i skjemaet som lagres.

Rekkefølge for hvor dataene leses fra:

1. JSON-LD (`<script type="application/ld+json">` med schema.org Product).
   Shopify, WooCommerce og de fleste moderne butikker har det.
2. Meta-tagger: `og:title`, `product:price:amount`, `itemprop="price"` osv.
3. `<title>` som siste utvei for navnet.

SSRF-vern. Dette er en tjeneste som henter en vilkårlig URL fra serveren, så
uten vern kunne en innlogget selger (eller noen med en stjålet økt) lese
interne tjenester: uvicorn-instansene på 127.0.0.1, MySQL-porter,
metadata-endepunkter og lignende. Derfor:

* bare http/https, og bare standardportene 80/443;
* vertsnavnet slås opp, og ALLE adressene det peker på må være offentlige
  (`ipaddress.is_global`). Ellers avvises det;
* tilkoblingen går til den adressen som ble sjekket, ikke til vertsnavnet på
  nytt. Ellers kunne et DNS-svar som endres mellom sjekken og tilkoblingen
  (DNS rebinding) sende oss til 127.0.0.1 likevel. TLS-sertifikatet sjekkes
  fortsatt mot vertsnavnet;
* omdirigeringer følges for hånd, maks 5, og hvert ledd sjekkes på nytt;
* svaret leses i biter med tak på størrelse (etter gzip-utpakking) og en
  samlet tidsfrist.
"""
import html as htmllib
import ipaddress
import json
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit

import certifi
import lxml.html
import urllib3

MAKS_BYTES = 3 * 1024 * 1024
MAKS_OMDIRIGERINGER = 5
FRIST_SEKUNDER = 15
MAKS_BESKRIVELSE = 5000   # tegn - samme tak som i tjeneste.manuell_produkt
AGENT = "Mozilla/5.0 (compatible; BWW-tilbud/1.0; +https://tilbud.betterworkwear.no)"


class HentFeil(ValueError):
    """Siden kunne ikke hentes eller leses. Meldingen vises til selgeren."""


# ------------------------------------------------------------------ henting

def _sjekk_url(url: str):
    """Returnerer (skjema, vert, sti) eller kaster HentFeil."""
    try:
        d = urlsplit(url.strip())
        port = d.port
    except ValueError:
        raise HentFeil("Lenken er ikke en gyldig adresse.")
    if d.scheme not in ("http", "https") or not d.hostname:
        raise HentFeil("Lenken må begynne med https:// eller http://.")
    if d.username or d.password:
        raise HentFeil("Lenker med brukernavn eller passord støttes ikke.")
    if port is not None and port != (443 if d.scheme == "https" else 80):
        raise HentFeil("Lenker med egen portangivelse støttes ikke.")
    try:
        vert = d.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise HentFeil("Vertsnavnet i lenken er ugyldig.")
    sti = (d.path or "/") + (f"?{d.query}" if d.query else "")
    return d.scheme, vert, sti


def _offentlig_adresse(vert: str, port: int) -> str:
    """Slår opp verten og returnerer én IP å koble til. Alle må være offentlige."""
    try:
        svar = socket.getaddrinfo(vert, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HentFeil(f"Fant ikke nettstedet «{vert}».")
    adresser = []
    for familie, _, _, _, sockaddr in svar:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_multicast:
            raise HentFeil("Lenken peker til en intern adresse og kan ikke hentes.")
        adresser.append((familie != socket.AF_INET, str(ip)))
    if not adresser:
        raise HentFeil(f"Fant ikke nettstedet «{vert}».")
    return sorted(adresser)[0][1]   # IPv4 først


def _hent(url: str, maks: int = MAKS_BYTES,
          accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5") -> (str, bytes):
    """GET med SSRF-vernet over. Returnerer (endelig URL, innhold).
    Brukes også av api/bilder.py til produktbilder fra andre nettsteder."""
    frist = time.monotonic() + FRIST_SEKUNDER
    for _ in range(MAKS_OMDIRIGERINGER + 1):
        skjema, vert, sti = _sjekk_url(url)
        https = skjema == "https"
        port = 443 if https else 80
        ip = _offentlig_adresse(vert, port)
        timeout = urllib3.Timeout(connect=5, read=10)
        if https:
            pool = urllib3.HTTPSConnectionPool(
                ip, port=port, timeout=timeout, retries=False,
                server_hostname=vert, assert_hostname=vert,
                cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())
        else:
            pool = urllib3.HTTPConnectionPool(ip, port=port, timeout=timeout, retries=False)
        try:
            r = pool.urlopen("GET", sti, redirect=False, preload_content=False, headers={
                "Host": vert, "User-Agent": AGENT, "Accept": accept,
                "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
            })
        except urllib3.exceptions.SSLError:
            raise HentFeil("Nettstedet har et ugyldig sikkerhetssertifikat.")
        except urllib3.exceptions.HTTPError:
            raise HentFeil("Fikk ikke kontakt med nettstedet.")
        try:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status >= 400:
                raise HentFeil(f"Nettstedet svarte med feil ({r.status}). "
                               "Noen butikker stenger ute automatiske oppslag - fyll inn feltene selv.")
            biter, lest = [], 0
            for bit in r.stream(64 * 1024, decode_content=True):
                lest += len(bit)
                if lest > maks:
                    raise HentFeil("Siden er for stor til å leses.")
                if time.monotonic() > frist:
                    raise HentFeil("Nettstedet brukte for lang tid på å svare.")
                biter.append(bit)
            return url, b"".join(biter)
        except urllib3.exceptions.HTTPError:
            raise HentFeil("Fikk ikke lest svaret fra nettstedet.")
        finally:
            r.release_conn()
            pool.close()
    raise HentFeil("For mange omdirigeringer.")


# ------------------------------------------------------------------ tolking

def tall(v) -> Optional[Decimal]:
    """«1 299,00», «1.299,00», «1299.00», «kr 1 299,-» → Decimal. None hvis ingen pris."""
    if v is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(v).replace("\xa0", " ").replace(",-", "")).strip("-")
    if not s:
        return None
    if "," in s and "." in s:
        # Det som står sist er desimalskillet.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # «1299,50» er desimal, «1,299» (engelsk tusenskille) er det ikke.
        hel, _, des = s.rpartition(",")
        s = (hel.replace(",", "") + "." + des) if len(des) != 3 else s.replace(",", "")
    elif s.count(".") > 1:
        # «1.299.000» - punktum som tusenskille. Ett enkelt punktum regnes som
        # desimal, slik JSON-LD og meta-taggene skriver det («1299.00»).
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d > 0 else None


def _typer(o: Dict):
    t = o.get("@type", "")
    return [x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _produkter(o):
    """Alle schema.org Product/ProductGroup-objekter, uansett hvor dypt de ligger."""
    if isinstance(o, list):
        for x in o:
            yield from _produkter(x)
    elif isinstance(o, dict):
        if any(t in ("product", "productgroup") for t in _typer(o)):
            yield o
        for k, v in o.items():
            if k in ("@graph", "mainEntity", "itemListElement", "item"):
                yield from _produkter(v)


def _pris_fra_tilbud(offers):
    """Laveste pris fra `offers`, med valuta. Håndterer Offer, AggregateOffer og lister."""
    beste = None
    for o in offers if isinstance(offers, list) else [offers]:
        if not isinstance(o, dict):
            continue
        spes = o.get("priceSpecification")
        spes = spes[0] if isinstance(spes, list) and spes else spes
        spes = spes if isinstance(spes, dict) else {}
        kandidater = [o.get("price"), o.get("lowPrice"), spes.get("price")]
        valuta = o.get("priceCurrency") or spes.get("priceCurrency")
        for k in kandidater:
            p = tall(k)
            if p and (beste is None or p < beste[0]):
                beste = (p, valuta)
    return beste


def _tekst(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("url") or ""
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _ren_tekst(v) -> str:
    """Beskrivelse til produktdatabladet som ren tekst. JSON-LD har ofte HTML
    eller HTML-entiteter i `description`; avsnittsskift beholdes."""
    if isinstance(v, list):
        v = " ".join(str(x) for x in v if isinstance(x, (str, int, float)))
    s = str(v or "").strip()
    if not s:
        return ""
    if "<" in s:
        s = re.sub(r"</(p|div|li|h[1-6])\s*>|<br\s*/?>", "\n\n", s, flags=re.I)
        try:
            s = lxml.html.fragment_fromstring(s, create_parent="div").text_content()
        except (ValueError, lxml.etree.ParserError):
            s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    s = "\n".join(re.sub(r"[ \t]+", " ", linje).strip() for linje in s.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", s).strip()[:MAKS_BESKRIVELSE]


def _variant(p: Dict, url: str) -> Dict:
    """Varianten i en ProductGroup som lenken peker på, ellers {}.

    Snickers (Hultafors) har én variant per farge, og lenken slutter på
    variantens sku (…/69400467). Gruppens første bilde er en annen farge."""
    sti = urlsplit(url).path.rstrip("/")
    for v in p.get("hasVariant") or []:
        if not isinstance(v, dict):
            continue
        vurl = urlsplit(_tekst(v.get("url"))).path.rstrip("/")
        sku = _tekst(v.get("sku"))
        if (vurl and vurl == sti) or (sku and sti.endswith("/" + sku)):
            return v
    return {}


def _fra_jsonld(doc, url: str = "") -> Dict:
    for s in doc.xpath('//script[contains(translate(@type,"JSONLD","jsonld"),"ld+json")]'):
        try:
            data = json.loads(s.text_content() or "", strict=False)
        except ValueError:
            continue
        for p in _produkter(data):
            v = _variant(p, url)
            gruppe = p.get("isVariantOf") if isinstance(p.get("isVariantOf"), dict) else {}
            beskrivelse = _ren_tekst(v.get("description") or p.get("description"))
            materiale = _ren_tekst(p.get("material") or v.get("material"))
            if materiale and materiale not in beskrivelse:
                beskrivelse = (beskrivelse + "\n\n" if beskrivelse else "") + f"Materiale: {materiale}"
            ut = {"navn": _tekst(p.get("name")), "beskrivelse": beskrivelse,
                  # Modellnummeret (`productGroupID`, f.eks. Snickers 6940) foran
                  # variantens sku (69400467 = 6940 + fargekode): tilbudet gis
                  # på plagget, ikke på én farge - samme prinsipp som katalogen.
                  "sku": _tekst(p.get("productGroupID") or gruppe.get("productGroupID")
                                or p.get("sku") or p.get("mpn") or p.get("productID")),
                  "bilde_url": _tekst(v.get("image") or p.get("image"))}
            pris = _pris_fra_tilbud(v.get("offers") or p.get("offers"))
            if not pris and p.get("hasVariant"):
                pris = _pris_fra_tilbud([v.get("offers") for v in p["hasVariant"]
                                         if isinstance(v, dict)])
            if pris:
                ut["pris"], ut["valuta"] = pris
            if ut["navn"] or pris:
                return ut
    return {}


def _meta(doc, *navn) -> str:
    for n in navn:
        for x in doc.xpath(f'//meta[@property="{n}" or @name="{n}" or @itemprop="{n}"]/@content'):
            if x.strip():
                return x.strip()
        for el in doc.xpath(f'//*[@itemprop="{n}"]'):
            v = el.get("content") or el.text_content()
            if v and v.strip():
                return re.sub(r"\s+", " ", v).strip()
    return ""


def _bilde_element(doc) -> str:
    """`itemprop="image"` på selve <img>-en (3X Workwear): adressen står i src, ikke i content."""
    for el in doc.xpath('//*[@itemprop="image"]'):
        v = el.get("content") or el.get("src") or el.get("href")
        if v and v.strip():
            return v.strip()
    return ""


def _beskrivelse_element(doc) -> str:
    """`itemprop="description"` på et element på siden, med avsnittene. Mer presist enn
    <meta name="description">, som hos 3X er en fellestekst for hele nettstedet."""
    for el in doc.xpath('//*[@itemprop="description"][not(self::meta)]'):
        tekst = _ren_tekst(lxml.html.tostring(el, encoding="unicode"))
        if tekst:
            return tekst
    return ""


def _you(doc) -> Dict:
    """
    you.no (grossist; pris bare for innloggede forhandlere). Ingen JSON-LD eller
    meta-tagger, men fast oppsett: varenummeret først i overskriften
    («P126 Denim Bib Apron»), produktbildet i bildeknappen, og ingress pluss
    «Egenskaper og materialer» / «Tekniske egenskaper» i et trekkspill.
    """
    ut = {}
    # Produktets overskrift - søkevinduet øverst på siden har også en <h1>.
    h1 = doc.xpath('//h1[contains(@class,"text-uppercase")]')
    # Bare overskriftens egen tekst: merker som «Nyhet» ligger i en <span class="badge"> inni.
    overskrift = _tekst(" ".join(h1[0].xpath("text()"))) if h1 else ""
    m = re.match(r"^([A-Z]{0,4}\d{2,}[A-Z0-9-]*)\s+(.+)$", overskrift)
    if m:
        ut["sku"], ut["navn"] = m.group(1), m.group(2)
    elif overskrift:
        ut["navn"] = overskrift
    bilde = doc.xpath('//a[@data-bs-target="#ProductDetailsImageModal"]//img/@src')
    if bilde:
        ut["bilde_url"] = bilde[0]
    deler = []
    for el in h1[0].xpath('following::div[contains(@style,"font-size: 1.25em")][1]') if h1 else []:
        deler.append(_ren_tekst(lxml.html.tostring(el, encoding="unicode")))
    for punkt in doc.xpath('//div[@id="product-details-accordion"]/div[contains(@class,"accordion-item")]'):
        tittel = _tekst(" ".join(punkt.xpath('.//*[contains(@class,"accordion-header")]//text()')))
        kropp = punkt.xpath('.//div[contains(@class,"accordion-body")]')
        tekst = _ren_tekst(lxml.html.tostring(kropp[0], encoding="unicode")) if kropp else ""
        if tekst and tittel.lower() != "sertifikater":
            deler.append(f"{tittel}:\n{tekst}" if tittel else tekst)
    if any(deler):
        ut["beskrivelse"] = "\n\n".join(d for d in deler if d)[:MAKS_BESKRIVELSE]
    return ut


def _newwave(doc) -> Dict:
    """newwave.no: varenummeret står bare i bildefilnavnet (…/5219806809_front.jpg),
    og tittelen er «Handlenett Sera | Bærenett | New Wave Norway AS» - første ledd er navnet."""
    ut = {"navn": _meta(doc, "og:title").split(" | ")[0].strip()}
    m = re.search(r"/(\d{6,})_[^/]*$", _meta(doc, "og:image"))
    if m:
        ut["sku"] = m.group(1)
    return ut


# Butikker uten standard produktmerking. Nøkkel: vertsnavn uten www.
# Det regelen returnerer, erstatter den generelle tolkingen; resten beholdes.
SPESIAL = {"you.no": _you, "newwave.no": _newwave}


def tolk(html: bytes, url: str = "") -> Dict:
    """Produktdata fra HTML. Egen funksjon slik at den kan testes uten nett."""
    # Uten <meta charset> leser lxml bytene som latin-1, og «Bærenett» ble
    # «BÃ¦renett» (newwave.no oppgir tegnsettet bare i HTTP-headeren). Er
    # innholdet gyldig UTF-8, er det UTF-8 - latin-1-tekst med æøå er det aldri.
    try:
        html.decode("utf-8")
        parser = lxml.html.HTMLParser(encoding="utf-8")
    except UnicodeDecodeError:
        parser = None
    try:
        doc = lxml.html.document_fromstring(html, parser=parser)
    except (ValueError, lxml.etree.ParserError):
        raise HentFeil("Siden kunne ikke leses som HTML.")

    ut = _fra_jsonld(doc, url)
    if not ut.get("navn"):
        ut["navn"] = _meta(doc, "og:title", "name") or _tekst(
            (doc.xpath("//title/text()") or [""])[0])
    if not ut.get("sku"):
        ut["sku"] = _meta(doc, "sku", "product:retailer_item_id", "mpn")
    if not ut.get("pris"):
        # «product:price.amount» med punktum: New Wave skriver det slik.
        ut["pris"] = tall(_meta(doc, "product:price:amount", "product:price.amount",
                                "og:price:amount", "price"))
        ut["valuta"] = _meta(doc, "product:price:currency", "product:price.currency",
                             "og:price:currency", "priceCurrency")
    if not ut.get("bilde_url"):
        ut["bilde_url"] = _meta(doc, "og:image") or _bilde_element(doc)
    if not ut.get("beskrivelse"):
        ut["beskrivelse"] = (_beskrivelse_element(doc) or _ren_tekst(_meta(doc, "og:description"))
                             or _ren_tekst(_meta(doc, "description")))

    # Butikkens egen regel sist, og den vinner: den er skrevet for akkurat den siden.
    vert = (urlsplit(url).hostname or "").removeprefix("www.")
    if vert in SPESIAL:
        ut.update({k: v for k, v in SPESIAL[vert](doc).items() if v})

    # Bildet vises bare som forhåndsvisning i skjemaet, det lagres ikke.
    # Bare http(s): en `javascript:`- eller `data:`-adresse fra siden skal ikke videre.
    bilde = urljoin(url, ut["bilde_url"]) if ut.get("bilde_url") else ""
    if urlsplit(bilde).scheme not in ("http", "https"):
        bilde = ""

    return {
        "navn": ut.get("navn", "")[:255],
        "sku": ut.get("sku", "")[:64],
        "pris": str(ut["pris"]) if ut.get("pris") else "",
        "valuta": (ut.get("valuta") or "").upper()[:3],
        "bilde_url": bilde,
        "beskrivelse": ut.get("beskrivelse", ""),
    }


def hent_produkt(url: str) -> Dict:
    """
    {navn, sku, pris, valuta, bilde_url, url}. Tomme strenger der ingenting ble funnet.

    `pris` er slik siden oppgir den. Norske butikker viser nesten alltid
    inkl. mva - skjemaet lar selgeren velge, og regner ned derfra.

    Uten innlogging: leverandørportaler som bare viser pris for innloggede
    kunder gir navn og varenummer, men ingen pris. Det er forventet - selgeren
    fyller inn prisen selv. Vi sender aldri cookies eller passord videre.
    """
    endelig, html = _hent(url)
    data = tolk(html, endelig)
    if not data["navn"] and not data["pris"]:
        raise HentFeil("Fant ingen produktdata på siden. Fyll inn feltene selv.")
    data["url"] = url.strip()
    return data
