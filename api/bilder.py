"""
Produktbilder fra BWW-katalogen.

Bildene ligger på SAMME server som denne tjenesten, under
`/var/www/ny.betterworkwear.no/data/media/catalog/product` (nettbutikken; stiene
er arvet fra Magento, WRKS-plagg under `wrks/`). De leses derfor rett fra disk -
ingen HTTP-kall, ingen autentisering, ingen ekstern avhengighet. Google
Apps Scriptet som denne tjenesten avløser måtte hente hvert bilde over
nett med `UrlFetchApp`; det slipper vi.

To ulike behov, to ulike veier:

* NETTLESEREN får en vanlig URL mot butikken (`bilde_url`). Butikken er
  offentlig, så det koster oss ingenting, og nettleseren cacher den.
* WORD-DOKUMENTET får en fil fra disk (`miniatyr`), fordi docxtpl bygger
  bildet inn i .docx-en.

Hvorfor vi lager egne miniatyrer og ikke bruker butikkens skalerte
varianter: de styres av butikkens bildeoppsett, kan skifte navn når det
endres, og kan tømmes når som helst. (Magento-butikken hadde nettopp det
problemet med `pub/media/catalog/product/cache/<hash>/`.) Et tilbud som
plutselig mangler bilder fordi noen ryddet i butikken er ikke akseptabelt.

Størrelsen er ikke kosmetikk. Originalene er PNG på 100-200 KB. Et tilbud
med 43 varelinjer ville blitt et Word-dokument på 7 MB, altså for stort for
e-post og tregt å redigere - og selgeren SKAL redigere det. Miniatyrene
lander på 3-6 KB.
"""
import hashlib
import io
import os
from typing import Optional

from PIL import Image

from api import ekstern

MEDIA_DIR = os.environ.get(
    "KATALOG_MEDIA_DIR", "/var/www/ny.betterworkwear.no/data/media/catalog/product")
MEDIA_URL = os.environ.get(
    "KATALOG_MEDIA_URL", "https://www.betterworkwear.no/media/catalog/product")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bildecache")
BREDDE = 180          # px - rikelig for ~18 mm i Word ved 250 dpi
KVALITET = 82
MAKS_EKSTERN = 15 * 1024 * 1024   # leverandørbilder er ofte store originaler (Bastad: 5376 px)


def _rel(sti: Optional[str]) -> Optional[str]:
    """Normaliserer en lagret bildesti. 'no_selection' (Magentos «intet bilde») vernes
    fortsatt mot, i tilfelle gamle data dukker opp - ingen rader har det per 21.10.2026."""
    if not sti or sti == "no_selection":
        return None
    return sti.lstrip("/")


def bilde_url(sti: Optional[str]) -> Optional[str]:
    """Offentlig URL mot butikken. Til nettleseren."""
    rel = _rel(sti)
    return f"{MEDIA_URL}/{rel}" if rel else None


def kildefil(sti: Optional[str]) -> Optional[str]:
    """Full sti til originalen på disk, eller None om den ikke finnes."""
    rel = _rel(sti)
    if not rel:
        return None
    full = os.path.join(MEDIA_DIR, rel)
    # Vern mot at en manipulert sti (../../) leser utenfor mediekatalogen.
    if not os.path.realpath(full).startswith(os.path.realpath(MEDIA_DIR) + os.sep):
        return None
    return full if os.path.isfile(full) else None


def miniatyr(sti: Optional[str], bredde: int = BREDDE) -> Optional[str]:
    """
    Sti til en nedskalert JPEG, generert ved behov og cachet lokalt.

    `bredde` i piksler: 180 til varelinja i pristabellen (~18 mm), 650 til
    databladene bakerst (~65 mm). Bredden inngår i cache-nøkkelen, så de to
    størrelsene lever side om side uten å overskrive hverandre.

    Returnerer None hvis produktet ikke har bilde, eller hvis fila ikke lar
    seg lese. Kallende kode skal tåle None - ett produkt i katalogen mangler
    bilde, og et tilbud skal ikke feile av den grunn.
    """
    kilde = kildefil(sti)
    if not kilde:
        return None
    return _skaler(kilde, _rel(sti), bredde)


def _ekstern_fil(url: str) -> str:
    return os.path.join(CACHE_DIR, "ekstern-" + hashlib.sha1(url.encode()).hexdigest())


def hent_ekstern(url: Optional[str]) -> bool:
    """
    Laster ned et produktbilde fra et annet nettsted (varer utenfor nettbutikken)
    og legger originalen i bildecachen. Returnerer om det finnes et brukbart bilde.

    Kalles når tilbudet LAGES, så bildet fryses sammen med prisen: forsvinner
    eller byttes det hos leverandøren, viser en regenerering fortsatt det samme.
    Nedlastingen går gjennom SSRF-vernet i api/ekstern.py, og fila må kunne
    åpnes som bilde av Pillow - ellers lagres ingenting.
    """
    if not url:
        return False
    fil = _ekstern_fil(url)
    if os.path.isfile(fil):
        return True
    try:
        _, data = ekstern._hent(url, maks=MAKS_EKSTERN, accept="image/*")
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(fil + ".tmp", "wb") as f:
            f.write(data)
        os.replace(fil + ".tmp", fil)
        return True
    except Exception:
        # Et manglende bilde skal ikke velte et tilbud.
        return False


def ekstern_miniatyr(url: Optional[str], bredde: int = BREDDE) -> Optional[str]:
    """Som miniatyr(), for et bilde hent_ekstern() allerede har lastet ned."""
    if not url:
        return None
    fil = _ekstern_fil(url)
    if not os.path.isfile(fil):
        return None
    return _skaler(fil, url, bredde)


def _skaler(kilde: str, navn: str, bredde: int) -> Optional[str]:
    st = os.stat(kilde)
    # mtime og størrelse i nøkkelen: byttes bildet ut i butikken uten at
    # filnavnet endres, lages miniatyren på nytt av seg selv.
    nokkel = hashlib.sha1(
        f"{navn}|{st.st_mtime_ns}|{st.st_size}|{bredde}".encode()
    ).hexdigest()
    ut = os.path.join(CACHE_DIR, f"{nokkel}.jpg")
    if os.path.isfile(ut):
        return ut

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with Image.open(kilde) as im:
            # PNG-ene har alfakanal. Uten en hvit bunn blir gjennomsiktige
            # partier svarte i JPEG - og i Word-dokumentet.
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bunn = Image.new("RGB", im.size, (255, 255, 255))
                bunn.paste(im, mask=im.split()[-1])
                im = bunn
            else:
                im = im.convert("RGB")

            if im.width > bredde:
                h = round(im.height * bredde / im.width)
                im = im.resize((bredde, h), Image.LANCZOS)

            midlertidig = ut + ".tmp"
            im.save(midlertidig, "JPEG", quality=KVALITET, optimize=True)
            os.replace(midlertidig, ut)   # atomisk - to samtidige kall kolliderer ikke
        return ut
    except Exception:
        # Et ødelagt bilde skal ikke velte et tilbud.
        return None
