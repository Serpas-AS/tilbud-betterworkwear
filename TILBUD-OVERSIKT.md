# Tilbudsløsningen tilbud.betterworkwear.no: teknisk overlevering

Formål: en utvikler skal kunne bygge en demo av det samme på en annen server.
Skrevet 30.10.2026 fra koden på `main`, skjemaet i databasen og driftsoppsettet på serveren.

Merking i dokumentet:

- **[Testet]**: kjørt under skrivingen, med oppdiktede data.
- **[Usikker]**: ikke bekreftet, eller bare delvis undersøkt.
- Alt annet er lest direkte i koden, men ikke nødvendigvis kjørt.

Dokumentet inneholder ingen hemmeligheter, kundedata eller reelle priser. Tallene i eksemplene er oppdiktet.

## Det viktigste først

Løsningen er enklere enn et fullt tilbudssystem. Den er et **internt selgerverktøy** som lager et Word-dokument.
Selgeren søker opp varer, setter rabatt eller fastpris og får et `.docx` på e-post. Selgeren justerer det selv
og sender det til kunden fra egen e-post.

| Finnes | Finnes ikke |
|---|---|
| Innlogging med engangskode, tre roller | Kundeportal, kundelenke, godkjenning, bestilling |
| Søk i nettbutikkens katalog | Kunderegister (kunden er fritekst på hvert tilbud) |
| Varer utenfor katalogen via produktlenke eller for hånd | Innpris, kostpris, margin, dekningsgrad |
| Rabatt i prosent eller fastpris per linje | Logo-, trykk- eller broderilinjer |
| Frosne priser, redigering, kopiering | Fraktberegning (bare en fast setning i malen) |
| Word med produktbilder og datablad | PDF til kunde |
| Status: sendt, vunnet, tapt | Valg av størrelse og farge på linjene |
| Excel-eksport for utvalgte brukere | Timbra-kobling (se del 4) |

Punktene i høyre kolonne er ikke glemt i dokumentet. Søk i koden gir ingen funksjonalitet for dem.
Skal demoen vise noe av dette, må det bygges nytt.

## 1. Stack, struktur, tjenester og repo

**Repo:** `git@github.com:Serpas-AS/tilbud-betterworkwear.git` (privat, bare `main`).
Tjenesten kjører rett fra arbeidsmappen `/var/www/tilbud.betterworkwear.no`.

**Stack:** Python 3.10, FastAPI, Uvicorn, Jinja2, MySQL 8 via `mysql-connector-python`, docxtpl og python-docx
for Word, Pillow for bilder, lxml og urllib3 for produktlenker, openpyxl for Excel, itsdangerous for økter,
requests for Mailgun. Nøyaktige versjoner står i `requirements.txt`.

Grensesnittet er serverrendret Jinja med vanlig JavaScript i malene. Det er ingen byggesteg, ingen `ui/static`
i git og ingen eksterne skript. CSS-en ligger i `base.html`. Det er ingen tester og ingen migrasjoner i repoet.

```text
api/
  main.py       Ruter, innlogging, økt, CSRF-sjekk
  tjeneste.py   Forretningslogikk, tilgang, status, brukeradmin
  prising.py    Prisregler for en linje og summering
  katalog.py    Leser produkter og priser fra nettbutikkens base (kun lesing)
  ekstern.py    Henter produktdata fra lenke, med SSRF-vern
  bilder.py     Miniatyrer og bildecache
  dokument.py   Word fra malen
  dataark.py    Produktdatablad bakerst i Word
  eksport.py    Excel-eksport
  epost.py      Mailgun (API eller SMTP)
  db.py         To databasetilkoblinger
maler/
  tilbud-mal.docx   Word-malen, med logoen innbakt
  omform-mal.py, sparelinje.py   Dokumentasjon av malendringer, ikke del av tjenesten
  logo.png, logo.svg  Kildefiler, brukes ikke av tjenesten
ui/templates/
  base, login, liste, nytt, detalj, brukere, _status, _logo.svg
generert/       Word-filene (ikke i git)
bildecache/     Miniatyrer og nedlastede bilder (ikke i git)
NOTAT.md        Utviklingslogg med begrunnelser (deler av den er utdatert, se del 9)
```

**Drift i dag**

| Del | Oppsett |
|---|---|
| Tjeneste | `tilbud.service`, bruker `tilbudweb`, `uvicorn api.main:app --host 127.0.0.1 --port 8002`, én prosess |
| Sandkasse | `ProtectSystem=strict`, bare `generert/` og `bildecache/` er skrivbare |
| nginx | TLS med certbot, `proxy_pass` til 8002, ratebegrensning på `/login*` (10 POST/min per IP) |
| Database | `tilbud` (lese og skrive) pluss `SELECT` på fire tabeller og utvalgte kolonner i `stores` i `bww_web` |
| Bilder | Leses fra nettbutikkens mappe på samme server, lesetilgang via ACL |
| E-post | Mailgun EU, HTTP-API foretrekkes, SMTP som reserve |
| Eksterne avhengigheter | Mailgun, og produktsidene ved lenkeoppslag |
| LibreOffice | Bare for utviklere som vil se dokumentet. Brukes ikke av tjenesten |

**Miljøvariabler** (verdier står i `.env`, som ikke er i git):

| Variabel | Må settes | Merknad |
|---|---|---|
| `TILBUD_DB_SOCKET`, `TILBUD_DB_USER`, `TILBUD_DB_PASSWORD`, `TILBUD_DB_NAME` | Ja | Tjenesten starter ikke uten. Koblingen går bare via Unix-socket, ikke TCP |
| `TILBUD_SECRET_KEY` | Ja | Signerer øktene |
| `TILBUD_URL` | **Ja for demo** | Standard er produksjonsadressen. Se fellen i del 9 |
| `KATALOG_DB_NAME` | Normalt | Standard `bww_web` |
| `KATALOG_MEDIA_DIR` | Normalt | Bilder fra disk, til Word |
| `KATALOG_MEDIA_URL` | Normalt | Bilder over HTTP, til nettleseren |
| `BUTIKK_URL` | Normalt | Grunnadresse for produktlenker i Excel |
| `KATALOG_MVA_SATS` | Nei | Standard 25 |
| `TILBUD_EKSPORT_EPOST` | Normalt | Kommaseparert. Standardverdien i koden er en navngitt adresse, så sett den |
| `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `SMTP_FROM` | For e-post | API-kanalen |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Alternativ | Brukes bare hvis API-nøkkelen mangler |

## 2. Selgerens flyt

| Steg | Hva skjer | Rute | Mal |
|---|---|---|---|
| 1. Innlogging, e-post | Selgeren oppgir e-post. Samme svar om brukeren finnes eller ikke | `GET /login`, `POST /login/kode` | `login.html` |
| 2. Innlogging, kode | Kode på formen `ABCD-2345`, gyldig i 10 min. Ny kode opphever gamle | `POST /login/verifiser` | `login.html` |
| 3. Oversikt | Tilbudsliste og tall per status, med treffprosent | `GET /` | `liste.html`, `_status.html` |
| 4. Nytt tilbud | Tomt skjema | `GET /nytt` | `nytt.html` |
| 5. Finn varer | Søk på varenr. eller navn, minst 2 tegn, maks 25 treff | `GET /api/sok` | `nytt.html` |
| 5b. Vare utenfor katalogen | Lim inn lenke og trykk «Hent», rett feltene, legg til. Kan også fylles ut for hånd | `POST /api/hent-url` | `nytt.html` |
| 6. Sett pris | Per linje: antall og rabatt %, fastpris eller ingen. Summene er et anslag i nettleseren | ingen serverrute | `nytt.html` |
| 7. Kunde og notat | Firma, kontakt, e-post, telefon, notat. Alt er fritekst, ingen felter er påkrevd | ingen serverrute | `nytt.html` |
| 8. Lagre | «Lag tilbud og send til meg» eller «Lagre». Serveren regner prisene, fryser linjene og lager Word | `POST /nytt` | ingen |
| 9. Tilbudssiden | Viser tilbudet, Word-nedlasting og status | `GET /tilbud/{id}` | `detalj.html` |
| 10. Last ned | Word-filen. Siden spør om tilbudet er sendt | `GET /tilbud/{id}/dokument` | `detalj.html` |
| 11. Status | generert → sendt → vunnet eller tapt, med angre | `POST /tilbud/{id}/status` | `_status.html` |

Andre ruter:

| Hva | Rute | Merknad |
|---|---|---|
| Rediger | `GET`, `POST /tilbud/{id}/rediger` | Samme skjema. Gamle linjer beholder frosne priser, nye får dagens pris. Word lages på nytt |
| Kopier | `GET /tilbud/{id}/kopier` | Fyller ut skjemaet for et nytt tilbud med dagens priser. Lagrer ingenting før selgeren trykker |
| Excel | `GET /tilbud/{id}/xlsx` | Bare for adressene i `TILBUD_EKSPORT_EPOST` |
| Brukeradmin | `GET`, `POST /brukere`, `POST /brukere/{id}` | Bare admin |
| Helsesjekk | `GET /frisk` | Rører ikke databasen |
| Utlogging | `GET /logout` | |

Tilbudsnummeret har formen `T-ÅÅÅÅ-NNNN`. Tilbudet er gyldig i 30 dager (`GYLDIGHET_DAGER` i `api/dokument.py`).

## 3. Datamodell

Det finnes ingen `CREATE TABLE`-filer i repoet. Skjemaet under er hentet fra databasen og forkortet.
Bruk det som utgangspunkt for demoen.

```sql
CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(190) NOT NULL UNIQUE,
  navn VARCHAR(190) NOT NULL DEFAULT '',
  telefon VARCHAR(40) NOT NULL DEFAULT '',
  rolle ENUM('selger','ledelse','admin') NOT NULL DEFAULT 'selger',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  butikk_id INT DEFAULT NULL,  -- bww_web.stores.id
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE login_codes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(190) NOT NULL,
  code VARCHAR(20) NOT NULL,
  expires_at DATETIME NOT NULL,
  used_at DATETIME DEFAULT NULL,
  KEY idx_email_code (email, code)
);

CREATE TABLE tilbud (
  id INT AUTO_INCREMENT PRIMARY KEY,
  tilbudsnr VARCHAR(20) NOT NULL UNIQUE,
  user_id INT NOT NULL,
  kunde_firma VARCHAR(190) NOT NULL DEFAULT '',
  kunde_kontakt VARCHAR(190) NOT NULL DEFAULT '',
  kunde_epost VARCHAR(190) NOT NULL DEFAULT '',
  kunde_telefon VARCHAR(40) NOT NULL DEFAULT '',
  notat TEXT,
  butikk VARCHAR(500) NOT NULL DEFAULT '',  -- navn og adresse, frosset
  sum_eks_mva DECIMAL(12,2) NOT NULL DEFAULT 0,
  sum_mva DECIMAL(12,2) NOT NULL DEFAULT 0,
  sum_inkl_mva DECIMAL(12,2) NOT NULL DEFAULT 0,
  status ENUM('generert','sendt','vunnet','tapt') NOT NULL DEFAULT 'generert',
  sendt_at DATETIME DEFAULT NULL,
  avgjort_at DATETIME DEFAULT NULL,
  docx_path VARCHAR(255) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user (user_id), KEY idx_status (status), KEY idx_created (created_at),
  CONSTRAINT fk_tilbud_user FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE tilbud_linje (
  id INT AUTO_INCREMENT PRIMARY KEY,
  tilbud_id INT NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  produkt_id INT DEFAULT NULL,           -- katalogens products.id, NULL for varer utenfor katalogen
  magento_id INT DEFAULT NULL,           -- arv, trengs ikke i demo, men koden leser kolonnen
  sku VARCHAR(64) NOT NULL,
  navn VARCHAR(255) NOT NULL,
  bilde VARCHAR(255) DEFAULT NULL,       -- relativ bildesti, fryst
  kilde_url VARCHAR(500) DEFAULT NULL,
  kilde_bilde_url VARCHAR(1000) DEFAULT NULL,
  kilde_beskrivelse TEXT,
  antall INT NOT NULL DEFAULT 1,
  listepris DECIMAL(12,2) NOT NULL,      -- eks. mva, fryst
  kampanjepris DECIMAL(12,2) NOT NULL,   -- eks. mva, fryst
  rabatt_type ENUM('ingen','prosent','fastpris') NOT NULL DEFAULT 'ingen',
  rabatt_prosent DECIMAL(5,2) DEFAULT NULL,
  fastpris DECIMAL(12,2) DEFAULT NULL,
  nettopris DECIMAL(12,2) NOT NULL,
  linjesum DECIMAL(12,2) NOT NULL,
  mva_sats DECIMAL(5,2) NOT NULL,
  KEY idx_tilbud (tilbud_id),
  CONSTRAINT fk_linje_tilbud FOREIGN KEY (tilbud_id) REFERENCES tilbud (id) ON DELETE CASCADE
);
```

**Kunder:** ingen egen tabell. Kunden er fire fritekstfelter på `tilbud`. «Nytt tilbud basert på dette» er
eneste gjenbruk.

**Produkter, varianter og priser** bor i nettbutikkens base og leses bare. Tilbudsløsningen kobler seg ikke til
noen fremmednøkkel der. Demoen trenger disse kolonnene:

| Tabell | Kolonner som brukes |
|---|---|
| `products` | `id, sku, name, type ('configurable'/'simple'), price, is_active, slug, category_id, description, teknisk_beskrivelse, vaskeanvisning, magento_id` |
| `product_variants` | `product_id, price, is_active, size, color_group, sort_order, id, ean, magento_id` |
| `product_images` | `product_id, filename, sort_order, id` |
| `categories` | `id, name` |

Sentrale prinsipper:

- **Tilbudet gis på plagget.** Linjene peker på `products`, aldri på en variant. Størrelse og farge lagres ikke.
- **Priser og bildesti fryses** på `tilbud_linje`. Beskrivende tekst i databladene slås opp på nytt hver gang
  Word lages.
- **Linjen er selvstendig.** Navn, varenummer og priser er kopiert inn, så gamle tilbud kan vises selv om
  varen er borte.

Den enkleste demoen er å lage de fire katalogtabellene med oppdiktede varer i en egen base og peke
`KATALOG_DB_NAME` dit. Da kan `api/katalog.py` brukes uendret.

## 4. Produktdata og bilder

**Hovedkilden er nettbutikkens base `bww_web`.** Tjenesten har bare `SELECT` på fire tabeller, pluss navn og adresse i `stores`.
Nettbutikken (`/var/www/ny.betterworkwear.no`, eget prosjekt) eier dataene. Tilbudsløsningen har ingen egen synk,
kopi, cache av produktdata eller planlagt jobb. Den leser katalogen direkte ved hvert søk.

Kilder bak katalogen, slik det ser ut fra nettbutikkens kode:

| Varer | Kilde | Oppdatering |
|---|---|---|
| WRKS | `https://www.wrks.no/api/katalog` | Timer `ny-bww-wrks.timer`, hver time. Priser, tekster, varianter og bilder. Se `api/integrations/wrks_katalog.py` i nettbutikken |
| Øvrige merker, blant annet Snickers Workwear | Engangsimport fra Magento, deretter admin i nettbutikken | **[Usikker]** Jeg så ingen automatisk synk for disse, men nettbutikkens admin og import er ikke gjennomgått |

**Timbra:** ingen spor i dette repoet. Det ligger ifølge oppdragsgiver på en annen server og er ikke undersøkt.
**Hvordan Timbra er koblet inn, må beskrives av den som kjenner den serveren.**

**Snickers** finnes på to måter:

1. Som vanlige varer i katalogen, merket `Snickers Workwear`.
2. Via produktlenke til snickersworkwear.no, se under. Linjen lagres da som vare utenfor katalogen.

Det er ingen API-integrasjon mot Snickers eller Hultafors.

**Varer utenfor katalogen (`api/ekstern.py`).** Lenken hentes fra serveren, og resultatet er bare et forslag som
selgeren retter. Det er tallene selgeren godkjenner som lagres. Tolkingen går i denne rekkefølgen:

1. JSON-LD (schema.org Product og ProductGroup).
2. Meta-tagger (`og:*`, `product:price:amount`, `itemprop`).
3. `<title>` for navnet.
4. Butikkregel i `SPESIAL`, som overstyrer det over.

| Nettsted | Særregel |
|---|---|
| snickersworkwear.no, solidgearfootwear.no, hellbergsafety.no | Norsk beskrivelse hentes fra siden |
| newwave.no | Varenummer fra bildefilnavnet |
| 3xworkwear.no | Varenummeret kuttes til de to første leddene. Pris krever innlogging |
| you.no | Egen tolking av siden. Pris krever innlogging |

Oppslaget har ingen innlogging, så forhandlerpriser må fylles inn for hånd. Butikker kan blokkere serverens
IP-adresse. Ingenting holdes oppdatert: en slik vare er en engangskopi, og lenken leses ikke på nytt.

**SSRF-vern** er innebygd og bør beholdes: bare http/https på port 80/443, alle DNS-svar må være offentlige,
tilkoblingen går til den sjekkede IP-adressen, og hver omdirigering sjekkes på nytt.

**Bilder** går to veier (`api/bilder.py`):

| Mottaker | Kilde |
|---|---|
| Nettleseren | Offentlig URL: `KATALOG_MEDIA_URL` + sti fra `product_images.filename` |
| Word | Fil fra `KATALOG_MEDIA_DIR`, skalert til JPEG i `bildecache/`: 180 px til linjene, 650 px til databladene |
| Varer utenfor katalogen | Lastes ned når tilbudet lages og lagres som `bildecache/ekstern-<sha1>` |

Mangler et bilde, blir linjen stående uten. Det velter ikke tilbudet, og det gir heller ingen feilmelding.

## 5. Prislogikken

Reglene ligger i `api/prising.py`. Serveren er sannhetskilden. Tallene i skjemaet er bare et anslag.

| Tema | Slik er det |
|---|---|
| **Innpris** | Finnes ikke. Ingen kolonne, ingen beregning |
| **Margin** | Finnes ikke. Selgeren ser ikke om et tilbud tjener penger |
| **Logo/trykk** | Finnes ikke. Kan i dag bare legges inn som manuell vare med egen pris, eller i notatet |
| **Frakt** | Beregnes ikke. Malen har den faste setningen «Frakt kommer i tillegg.» |
| **Veil. pris (`listepris`)** | Se under |
| **Rabatt** | Prosent 0–100, regnes av `kampanjepris` |
| **Fastpris** | Erstatter rabatten. Kan være 0 og kan ligge over veil. pris |
| **Ingen rabatt** | Nettopris = `kampanjepris` |
| **Mva** | Katalogen er inkl. mva og regnes ned til eks. mva. Tilbudet vises eks. mva, med mva og sum inkl. mva nederst |
| **Avrunding** | Halv opp til øre, per linje, deretter summering |

Utregning:

```text
listepris eks. mva    = katalogpris inkl. mva / (1 + mva_sats/100)
rabatt:   nettopris   = kampanjepris × (100 − rabatt) / 100
fastpris: nettopris   = fastpris
linjesum              = nettopris × antall
sum_mva               = summen av (linjesum × mva_sats / 100), avrundet per linje
sum_inkl_mva          = sum_eks_mva + sum_mva
Du sparer             = (listepris × antall, summert) − sum_eks_mva
```

**Hvor listepris og kampanjepris kommer fra (`api/katalog.py`):**

| Produkt | Listepris | Kampanjepris |
|---|---|---|
| `simple` | `products.price` | `products.price` |
| `configurable` med aktive variantpriser | Høyeste variantpris | Laveste variantpris |
| `configurable` uten variantpris | `products.price` | `products.price` |

Eksempel med oppdiktede tall, kjørt gjennom koden **[Testet]**: veil. 1000, kampanjepris 900, 10 % rabatt,
10 stk. gir 810,00 per stykk og 8 100,00 i linjesum. Med en linje til på fastpris 500 blir sum eks. mva 8 600,00,
mva 2 150,00 og sum inkl. mva 10 750,00. Word viser «Du sparer −2 400,00 kr (22 %)».

Regler som håndheves på serveren:

- Rabatt og fastpris kan ikke brukes samtidig.
- Antall må være minst 1, og tilbudet må ha minst én vare.
- Nettopris 0 avvises, med mindre fastpris er satt eksplisitt.
- Varer utenfor katalogen må ha navn og pris over 0. Prisen oppgis inkl. eller eks. mva.
- Det finnes ingen tak på rabatt per rolle og ingen godkjenning av store rabatter.

**[Usikker]** «Kampanjepris» betyr i praksis laveste variantpris, ikke en kampanje. Blant aktive konfigurerbare
produkter er det nesten ingen med ulik variantpris. Nettbutikken har egne tabeller `kampanjer` og `kampanje_priser`.
Tilbudsløsningen leser dem ikke og har ikke tilgang til dem, og jeg har ikke sett hvordan de virker. Ekte
kampanjer slår derfor trolig ikke inn i tilbud. Avklar dette før noen lover kampanjepriser i et tilbud.

**[Usikker]** `NOTAT.md` sier at rabatt av kampanjepris og avrunding per linje er bevisste valg som ikke er bekreftet.

## 6. Hva kunden får

Kunden får **det selgeren sender**. Systemet har ingen kontakt med kunden.

| Spørsmål | Svar |
|---|---|
| PDF | Nei. Word er et bevisst valg, fordi selgeren skal kunne redigere. Selgeren kan lagre som PDF selv |
| Lenke til kunden | Nei. Lenken i e-posten går til selgerens innloggede tilbudsside |
| Godkjenning | Nei |
| Bestilling | Nei, og ingen kobling til ordre eller ERP |
| Sending til kunde | Nei. E-post går bare til selgeren. `kunde_epost` skrives bare på dokumentet |

**Word-dokumentet lages slik:**

1. `api/dokument.py` fyller `maler/tilbud-mal.docx` med docxtpl. Layout og logo ligger i malen, som kan åpnes i Word.
2. Malen har logo, tilbudsnr., dato og gyldig til, kunde og kontaktperson, varetabell med bilde, veil. pris, din pris
   og sum, og deretter sumtabell. Sparelinjen vises bare når totalen er under veil. pris.
3. `api/dataark.py` legger til ett datablad per vare i kode, etter malen: navn, varenr., bilde, din pris,
   beskrivelse, teknisk informasjon, vaskeanvisning og EAN. Tomme felter utelates.
4. Filen lagres som `generert/<tilbudsnr>.docx`. Redigering overskriver den.

Rendering med oppdiktede data og uten database virker, og konvertering til PDF via LibreOffice ga riktig oppsett,
også med `&` i kundenavnet **[Testet]**. Sjekk utseendet i Word før møtet. Det er ikke testet her.

Feiler databladene, logges det, og tilbudet blir laget uten dem.

**Excel-eksport (`api/eksport.py`):** bruker kolonneoppsettet til regnearkene som importeres i en annen
tilbudsløsning. Rabattprosenten regnes mot veil. pris. Størrelser, farge og kategori slås opp ved eksport.

## 7. Innlogging, roller og tilgang

**Innlogging** er passordløs engangskode på e-post. Det er ingen selvregistrering. Økten er en signert cookie
(`httponly`, `secure`, `samesite=lax`), gyldig i 30 dager, men brukeren leses fra databasen ved hver forespørsel.
En deaktivering virker derfor med en gang.

| Handling | selger | ledelse | admin |
|---|---|---|---|
| Lage tilbud | Ja | Ja | Ja |
| Se, redigere, kopiere og laste ned egne tilbud | Ja | Ja | Ja |
| Sette status på egne tilbud | Ja | Ja | Ja |
| Se og endre andres tilbud | Nei (404) | Ja | Ja |
| Se selgerkolonnen i oversikten | Nei | Ja | Ja |
| Brukeradministrasjon | Nei (403) | Nei (403) | Ja |
| Excel-eksport | Bare e-postliste | Bare e-postliste | Bare e-postliste |

Tilgangen til tilbud håndheves i `tjeneste.hent_tilbud()` og `synlige_tilbud()`, ikke i rutene.
Excel styres av e-postadresse, ikke rolle. Brukere deaktiveres i stedet for å slettes. Den siste aktive
administratoren kan ikke deaktiveres eller miste rollen. Navn og telefon på brukeren havner på dokumentet.

**Vern:** alle POST krever at `Origin` eller `Referer` er lik `TILBUD_URL`. Malene escaper verdier, og Word rendres
med `autoescape=True`.

**[Usikker]** Selve appen teller ikke feilforsøk på koden. Begrensningen ligger bare i nginx, og den bruker en
nøkkel definert i en annen nginx-fil. En demo må sette opp sin egen begrensning hvis den er åpen på nett.

## 8. Spesifikt for Better Workwear og hva som kan gjenbrukes

**Kan brukes direkte:**

| Del | Filer |
|---|---|
| Prislogikk | `api/prising.py` |
| Tilbudsflyt, status, roller, brukeradmin | `api/tjeneste.py` |
| Innlogging, økt, CSRF | `api/main.py`, `login.html` |
| Skjema, oversikt, tilbudsside | `nytt.html`, `liste.html`, `detalj.html`, `brukere.html`, `_status.html` |
| Word-motoren og databladene | `api/dokument.py`, `api/dataark.py` (bytt farger og etiketter ved behov) |
| Bildecache | `api/bilder.py` |
| Generell lenkehenting og SSRF-vern | `api/ekstern.py` |
| E-post | `api/epost.py` (API-adressen er Mailguns EU-sone) |

**Må byttes eller justeres:**

| Hva | Hvor |
|---|---|
| Logo | `ui/templates/_logo.svg`, og inne i `maler/tilbud-mal.docx` |
| Navn i grensesnitt og e-post | `base.html`, `login.html` (lenke og tittel), `brukere.html` (plassholder og domene), `epost.py`, `main.py` (`title`) |
| Farger | `base.html`, `api/dataark.py`, `api/epost.py` |
| Vilkår og gyldighet | Bunnteksten i malen, `GYLDIGHET_DAGER` i `api/dokument.py` |
| Katalogkobling | `api/katalog.py` er skrevet for skjemaet i `bww_web` |
| Standardverdier i koden | `TILBUD_URL`, `KATALOG_*`, `BUTIKK_URL`, `TILBUD_EKSPORT_EPOST` og `AGENT` i `api/ekstern.py` |
| Butikkregler | `SPESIAL` i `api/ekstern.py`, knyttet til BWWs leverandører |
| Excel | Kolonnene passer et bestemt importformat |
| Tilbudsnummer | `_neste_tilbudsnr()` i `api/tjeneste.py` |

**Bør ikke følge med til en ekstern demo uten gjennomgang:** `NOTAT.md` og docstringen i `api/eksport.py`. De
inneholder navn, driftsdetaljer om serveren og eksempler fra drift. Ta heller ikke med `generert/` eller
`bildecache/`, og aldri en kopi av `tilbud`-basen. Den inneholder ekte kunder og priser.

## 9. Uferdig, skjørt og hva som må fungere i møtet

### Fellene når demoen settes opp

1. **`TILBUD_URL` må være demoens nøyaktige adresse.** Ellers gir alle POST, også innlogging, 403 med «Forespørselen
   kom ikke fra tilbud-siden» **[Testet]**. Uten skråstrek til slutt, og med riktig `https://`.
2. **Bruk HTTPS.** Øktcookien er `secure`, så innlogging over ren HTTP virker ikke. Kjør bak nginx med sertifikat.
   Behold `--proxy-headers`.
3. **Innlogging krever e-post som virker.** Uten Mailgun forsvinner koden uten feilmelding til brukeren, og den skrives
   ikke til loggen. Nødløsning: les siste rad i `login_codes` for demobrukeren.
4. **Første admin må legges inn med SQL.** Det finnes ingen oppsettkommando:
   `INSERT INTO users (email, navn, telefon, rolle) VALUES ('demo@example.com', 'Demo Selger', '', 'admin');`
5. **Databasen må nås via Unix-socket.** TCP eller annen vert krever en endring i `api/db.py`.
6. **Skjemaet må lages for hånd.** Bruk del 3.
7. **Bilder trenger begge kildene.** Uten `KATALOG_MEDIA_URL` blir bildene ødelagte i skjemaet. Uten
   `KATALOG_MEDIA_DIR` mangler de i Word, uten feilmelding.
8. **`generert/` og `bildecache/` må være skrivbare** for tjenestebrukeren.
9. **Python-endringer krever omstart.** Malene i `ui/templates` lastes på nytt med en gang.
10. **Tjenesten kan startes uten database.** `/frisk` og `/login` svarer da 200, mens innlogging gir 500 **[Testet]**.
    En grønn helsesjekk beviser derfor lite.

### Skjørt i koden

| Svakhet | Konsekvens |
|---|---|
| `opprett_tilbud` har ingen transaksjon, og basen kjører `autocommit` | Feiler Word etter lagring, står tilbudet igjen uten dokument, og nummeret er brukt |
| Serverfeil gir tekstsvar, mens skjemaet forventer JSON | Knappen kan bli stående på «Lagrer tilbud …» uten melding, og selgeren må laste siden på nytt |
| Rutene er `async`, men gjør blokkerende arbeid i én prosess | Word, bildenedlasting og Mailgun holder igjen andre brukere. Hver e-post kan ta opptil 25 sekunder |
| Tilbudsnummer lages uten lås | Samtidig lagring kan treffe `UNIQUE` og gi 500 |
| Oversikten viser maks 200 tilbud | Tallene og treffprosenten gjelder bare disse. Det er heller ingen søk eller filter |
| Ingen versjonshistorikk | Redigering overskriver dokumentet. Bare status «sendt» gir en advarsel, ikke vunnet eller tapt |
| Ingen sletting av tilbud | Testtilbud blir liggende, så bruk en egen demobase |
| Tomt skjema etter sletting av linjer | Lest i koden, ikke testet: rabattradene i bunnen skjules trolig ikke når siste linje fjernes |
| EAN kommer sjelden med | Krever en variant med samme `magento_id` som produktet, så konfigurerbare varer får den normalt ikke |
| Lenkehenting avhenger av fremmede nettsider | Klassenavn og oppsett kan endres, og serverens IP kan bli blokkert |
| Ingen tester | Prisreglene er bare sikret av manuell testing |
| `NOTAT.md` er delvis utdatert | Blant annet «Utestående» om malen. `api/db.py` nevner tre katalogtabeller, men tilgangen gjelder fire |
| `maler/omform-mal.py` og `sparelinje.py` | Skal ikke kjøres på nytt. Sikkerhetskopiene de viser til, er ikke i git |

### Sjekkliste for møtet

Dette må virke:

- [ ] Innloggingskode kommer fram raskt, eller demobrukeren er allerede logget inn.
- [ ] Søk gir treff med bilder på ord du har øvd på.
- [ ] Varer med rabatt og fastpris gir summer som stemmer.
- [ ] «Lag tilbud» går uten feil, med en kunde med oppdiktet navn.
- [ ] Word-filen åpner på møtemaskinen, med logo, bilder, sparelinje og datablad. Nettleseren viser den ikke, så ha Word
      klart, eller ha en ferdig PDF som reserve.
- [ ] E-posten med vedlegg kommer fram, og den havner ikke i søppelpost.
- [ ] Status kan settes til sendt og vunnet, og tallene i oversikten endrer seg.
- [ ] Rollene vises med to brukere: en selger og en leder.
- [ ] Oversikten er fylt med noen ferdige, oppdiktede tilbud i ulike statuser.

Slik reduseres risikoen:

- **Forbered produktlenkene.** Test dem fra demoserveren samme dag, og ha én lenke som virker og feltene klare for
  hånd. Serverens IP avgjør om butikkene svarer.
- **Vis dokumentet fra en ferdig fil som reserve** hvis nett eller e-post svikter.
- **Ikke vis innpris, margin, kundelenke eller PDF.** De finnes ikke. Skal de med, må de bygges først.
- **Ikke bruk ekte kundenavn eller priser** i demodata.
- **Sjekk bunnteksten i malen**, slik at setningen om frakt passer for demoen.

## 10. Filene som er viktigst å lese

Les dem i denne rekkefølgen:

1. `api/tjeneste.py`: tilbudsflyten, frysing av priser, tilgang og status.
2. `api/prising.py`: alle prisreglene, kort og samlet.
3. `api/main.py`: alle ruter, innlogging, økt og CSRF.
4. `api/katalog.py`: hvordan pris og produkt leses fra katalogen, og mva-omregningen.
5. `ui/templates/nytt.html`: selgerens skjema og all logikken i nettleseren.
6. `api/dokument.py`: hvordan Word lages.
7. `maler/tilbud-mal.docx`: malen. Åpne den i Word og se feltene.
8. `api/dataark.py`: databladene.
9. `api/ekstern.py`: lenkehenting, butikkregler og SSRF-vern.
10. `NOTAT.md`: begrunnelsene. Les den som historikk, ikke som fasit.
