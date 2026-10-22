# tilbud.betterworkwear.no

Tilbudsgenerator for selgere. Bygget 31.08.2026.

---

## ▶ START HER

> **22.10.2026: tilbud kan redigeres** («Rediger tilbudet» på tilbudssiden, `/tilbud/{id}/rediger`).
> Samme skjema som nytt tilbud, ferdig utfylt. Linjer som står igjen beholder de frosne prisene
> (`linje_id` → `tjeneste.frosset_produkt`); nye varer får dagens pris. Samme tilbudsnr., dato og
> selger på dokumentet; Word lages på nytt og sendes til den som redigerte. Ingen versjonshistorikk.
> «Nytt tilbud basert på dette» (`/tilbud/{id}/kopier`) fyller ut skjemaet for et NYTT tilbud: kunde,
> antall og rabatt kopieres, men katalogvarer får dagens pris. Varer som er borte fra katalogen listes.
>
> **Produktlenker, testet 22.10.2026** (`api/ekstern.py`): Snickers, Solid Gear og Hellberg gir alt via
> JSON-LD. New Wave og you.no har egne regler (`SPESIAL`). **3X Workwear og you.no viser ikke pris uten
> innlogging** (forhandlerpriser) - selgeren fyller inn prisen; resten hentes. 3X: varenummeret kuttes
> til modellen = de to første leddene (60111-414-1117-2424 → 60111-414), avtalt med Morten 22.10.2026.
> Snickers/Solid Gear/Hellberg (`_hultafors`): beskrivelsen tas fra sidens norske tekst, ikke JSON-LD
> (kort hos Solid Gear, engelsk hos Snickers).

> **03.10.2026, sikkerhet:** kjører som egen bruker `tilbudweb` (ikke `www-data`) i systemd-sandkasse;
> koden eies av root, bare `generert/` og `bildecache/` er skrivbare (0750/0640). Katalogbildene i
> ny-bww leses via ACL. Alle POST krever `Origin: https://tilbud.betterworkwear.no` (CSRF), økter
> utløper etter 12 t på serveren, Word-malen rendres med `autoescape=True`. Ingen tilgang til `bbw`.
> Koden ligger i lokalt git. Gjennomgang og backup: `/root/tilbud-sikkerhet-2026-10-03/`.

> **03.10.2026: katalogen leses fra `bww_web`, ikke Magento (`bbw`).** betterworkwear.no
> går over til FastAPI (/var/www/ny.betterworkwear.no), og bww-Magento skal fjernes.
> Identiteten på linjene er nå `produkt_id` (= `bww_web.products.id`); `magento_id`
> står igjen på de 142 linjene fra før byttet. Priser verifisert like for alle
> BWW-produkter; WRKS-plagg har navn/tekst fra wrks-katalogen, som nettsiden.
> Bilder fra `/var/www/ny.betterworkwear.no/data/media`. `tilbud` har bare SELECT på
> products/product_variants/product_images i bww_web. Punkt 1 under (Serpas_SalesQuote)
> utgår når Magento fjernes. Backup og fasit: /root/tilbud-bytte-2026-10-03/.

*Sist oppdatert 01.09.2026, midt på dagen. Detaljene for alt som er gjort
ligger under «Endret 01.09.2026» lenger nede.*

Tjenesten er **i drift og verifisert**. Innlogging, produktsøk, rabatt/fastpris,
Word-generering med logo, produktbilder og datablad, e-post til selgeren,
rollestyring og brukeradministrasjon — alt testet ende-til-ende.

Ett ekte tilbud i basen: **T-2026-0001, Byggmester Bob**, laget av sjefen hos
BWW 01.09 kl. 09:19. Det er fra *før* redesignet og databladene, så det ser
annerledes ut enn nye tilbud. Ikke slett det.

### Køen, i prioritert rekkefølge

~~**1. `Serpas_SalesQuote`**~~ — **utgår** (Morten 22.10.2026): gammel Magento-modul som i praksis
aldri ble brukt, og funksjonaliteten trengs ikke. Ikke bygg noen innboks for den.

~~**2. Rabattotal i selve Word-dokumentet.**~~ — **gjort 22.10.2026.** Sumtabellen har
«Veil. pris eks. mva» og «Du sparer −X kr (Y %)» over «Sum eks. mva», bare når totalen er
under veil. (`dokument._sparing`, malendring i `maler/sparelinje.py`, backup
`maler/tilbud-mal.docx.bak-sparer-2026-10-22`). Måles mot veil., så kampanje regnes med.

**3. Legg inn de øvrige selgerne** under /brukere. Husk navn og telefon, de
havner på tilbudsdokumentene.

### Designrunder går fort nå

LibreOffice er installert, så dokumentet kan ses under arbeid:

```bash
soffice --headless --convert-to pdf --outdir <dir> <fil>.docx
pdftoppm -r 100 -png <fil>.pdf side
```

Møtet hos BWW 01.09 kan gi tilbakemeldinger på oppsettet — da tas de her.

### Løse tråder som ikke haster

- ~~Magento sender ordrebekreftelser fra `kundeservice@email.betterworkwear.no`,
  som ikke finnes i Mailgun.~~ Utgår med Magento: ny.betterworkwear.no sender
  via Mailgun-API-et på det verifiserte domenet `betterworkwear.no`.
- **Uavklart i prislogikken:** rabatten regnes av `final_price` (dagens faktiske
  pris), ikke av listepris, og avrundes per linje. Bevisste valg, ikke bekreftet
  av Morten. Se `api/prising.py`. Merk at UI-et *viser* avviket fra veil. pris,
  og skiller ut kampanjedelen for seg, så selgeren ikke tror han ga bort mer
  enn han gjorde.

---

## Endret 21.10.2026: Excel-eksport

Knappen «Eksporter til Excel» på tilbudssiden (`/tilbud/{id}/xlsx`,
`api/eksport.py`) gir et regneark med samme kolonner som arkene som
importeres i tilbudsløsningen på den andre serveren (mal:
`/home/magento/ny-better/THORSTEINSEN.xlsx`).

- **Bare for `TILBUD_EKSPORT_EPOST`** (kommaseparert, standard
  `morten@serpas.no`). Håndheves i ruta, ikke bare ved å skjule knappen.
- Veil. pris og Kundepris er eks. mva fra de frosne linjene. Rabatt er
  prosent i forhold til veil. pris, slik at malens Kundepris = Veil. ×
  (1 − Rabatt) stemmer også ved fastpris og kampanjepris.
- Størrelser, farge (`color_group`), kategori og lenke slås opp i `bww_web`
  nå. Derfor har `tilbud` fått `SELECT` på `bww_web.categories` i tillegg.
- Tekst som begynner med «=» lagres som tekst, ikke formel. Navn fra
  eksterne sider skal ikke kunne bli formler i regnearket.
- Ny avhengighet: `openpyxl` (i `requirements.txt`).

## Endret 21.10.2026: varer utenfor nettbutikken

Selgerne gir også tilbud på varer som ikke finnes i `bww_web`. Under
«Vare som ikke er i nettbutikken» i skjemaet limer de inn lenken til
produktsiden. `api/ekstern.py` foreslår da varenummer, navn og pris fra
JSON-LD og meta-tagger, og selgeren retter feltene før varen legges til.
Feltene kan også fylles ut helt for hånd.

- Det er prisen **selgeren godkjenner** som lagres, ikke det som ble hentet.
  Prisen oppgis inkl. eller eks. mva, og regnes om med `KATALOG_MVA_SATS`.
- **Ingen innlogging:** priser som bare vises for innloggede kunder
  (leverandørportaler) kan ikke hentes. Da kommer navn og varenummer, men
  ikke pris.
- Mange store butikker (Biltema, Shopify-butikker bak Cloudflare o.l.) avviser
  oppslag fra serverens IP-adresse med 403/429. Selgeren får beskjed og fyller
  ut selv. Det hjelper ikke å late som man er en nettleser - prøvd.
- Linja lagres med `produkt_id = NULL` og ny kolonne `tilbud_linje.kilde_url`.
- **Varenummer:** `productGroupID` (modellen, f.eks. Snickers 6940) går foran
  variantens `sku` (69400467 = modell + fargekode). Bilde og pris tas fra
  varianten lenken peker på. CSS-klasser som `product-card_code__gmhTj` er
  med vilje ikke brukt - endelsen byttes ved hver bygg av nettstedet.
- **Produktdatablad:** beskrivelsen (JSON-LD `description` + `material`,
  ellers `og:description`) foreslås i skjemaet og lagres som ren tekst i
  `tilbud_linje.kilde_beskrivelse`. Bare linjer MED beskrivelse får ark.
- **Bilde:** foreslås fra siden (JSON-LD / `og:image`) og kan rettes eller
  limes inn. Adressen lagres i `tilbud_linje.kilde_bilde_url`, og bildet
  lastes ned når tilbudet lages (`bilder.hent_ekstern`, samme SSRF-vern,
  maks 15 MB, må kunne åpnes av Pillow). Originalen fryses som
  `bildecache/ekstern-<sha1 av URL>`. Feiler nedlastingen, beholdes adressen
  (Excel-eksporten bruker den), men Word-dokumentet får ikke bilde.
- **SSRF-vern** i `api/ekstern.py`: bare http/https på port 80/443, alle
  DNS-svar må være offentlige adresser, tilkoblingen går til den sjekkede
  IP-en (vern mot DNS rebinding), og hver omdirigering sjekkes på nytt.
  Endepunktet er POST, så samme-opphav-sjekken gjelder.

## Endret 01.09.2026

> **Historikk fra Magento-tiden.** Det som står her om `visibility`,
> `catalog_product_super_link`, `pub/media` og antall produkter gjaldt
> Magento-katalogen (`bbw`). Fra 03.10.2026 leses `bww_web` - se docstringen
> i `api/katalog.py`. Prinsippene (plagg, ikke størrelser; frosne priser og
> bilder; egne miniatyrer) gjelder fortsatt.

**Søket viser ikke lenger størrelsesvarianter.** Et tilbud gis på plagget, ikke
på «str. L». `api/katalog.sok()` filtrerer nå på `visibility <> 1`
(Not Visible Individually) og dropper i tillegg alt som er barn i
`catalog_product_super_link`. Søket ser 439 produkter i stedet for 4 464.

Hvorfor `visibility` og ikke `type_id = 'configurable'`: 75 av varene er
frittstående simple — hansker og tilbehør uten konfigurerbar forelder — og de
er legitime tilbudsvarer. Og hvorfor ikke bare fravær av forelder: 75
varianter er **foreldreløse** i `catalog_product_super_link` (alle
«Sky Captain»- og «Goji Berry»-fargene av WR10104, f.eks.) og ville sluppet
gjennom. `visibility` fanger begge deler. Se docstringen i `katalog.py`.

`hent()` er bevisst ikke filtrert — eldre tilbud kan peke på en variant og
skal fortsatt kunne åpnes.

**Produktbilder, i UI-et og i dokumentet.** Miniatyr i søketreffene (44 px),
i varelinjene (40 px) og i Varenr.-kolonnen i Word-dokumentet (18 mm).

Bildene ligger på samme server (til 03.10.2026 `/var/www/bww/pub/media/catalog/product`,
nå `/var/www/ny.betterworkwear.no/data/media/catalog/product`), og
leses rett fra disk — ingen HTTP-kall. Nettleseren får en vanlig URL mot
butikken; dokumentet får en fil.

`api/bilder.py` lager egne miniatyrer (180 px, JPEG) og cacher dem i
`bildecache/`. Vi bruker med vilje **ikke** Magentos egne cache-varianter i
`pub/media/catalog/product/cache/<hash>/`: hash-katalogene er utledet av
bildekonfigurasjonen, skifter navn når den endres, og kan tømmes av
`catalog:images:resize`. Et tilbud som mister bildene fordi noen kjørte en
cache-kommando i butikken er ikke akseptabelt.

Størrelsen er ikke kosmetikk: originalene er 100–200 KB PNG, så 43 varelinjer
ville gitt et Word-dokument på 7 MB — for stort for e-post og tregt å redigere,
og selgeren *skal* redigere det. Miniatyrene er 3–4 KB, og et tilbud med tre
linjer lander på 82 KB.

PNG-ene har alfakanal og flates mot hvit bunn før JPEG-lagring; uten det blir
gjennomsiktige partier svarte i Word.

Bildestien fryses på tilbudslinja (`tilbud_linje.bilde`, lagt til 01.09.2026)
av samme grunn som prisene: en regenerering skal vise det kunden faktisk fikk.
Ett produkt i katalogen mangler bilde — linja rendrer da uten, ikke med en
ødelagt boks.

**Rendering: LibreOffice er installert 01.09.2026** (`libreoffice-writer`,
`--no-install-recommends`, 62 pakker / ~284 MB). Ingen tjeneste, ingen port —
den kjører kun som engangsprosess når den kalles. Formålet er å kunne se
dokumentet under designarbeid i stedet for å gjette:

    soffice --headless --convert-to pdf --outdir <dir> <fil>.docx
    pdftoppm -r 100 -png <fil>.pdf side

Tre feil kom først til syne da dokumentet faktisk ble tegnet opp, og ingen av
dem var synlige i XML-en:

1. Alle seks kolonnene var 29 mm. «BESKRIVELSE» brakk midt i ordet mens
   Varenr. hadde plass til overs. Nå 24/52/20/24/26/28 mm.
2. Tallkolonnene var venstrestilt. I en pristabell må tall stå på høyre kant,
   ellers kan de ikke sammenlignes nedover.
3. En **helt blank side** mellom tilbudet og første datablad. Årsaken var at
   sideskiftet lå i sitt eget avsnitt; når side 1 er full, flyter det avsnittet
   selv over på side 2 og skiftet skyver innholdet til side 3. Rettet med
   `page_break_before` på arkets første avsnitt, så det ikke finnes noe ekstra
   avsnitt som kan flyte.

Merk at `maler/omform-mal.py` nå er **idempotent** — første versjon la inn en ny
`<w:pBdr>` hver gang den kjørte, så en ekstra kjøring ga duplikat i samme `pPr`.
Riktig framgangsmåte ved endring: kopier `tilbud-mal.docx.bak-design-2026-09-01`
over `tilbud-mal.docx` og kjør skriptet på nytt, slik at det alltid går på en
ren mal.

**Moderne oppsett i malen (01.09.2026).** Begge tabellene brukte Words
`TableGrid` — heltrukket svart rutenett rundt hver eneste celle. Det er fjernet.

Prinsippet nå: en linje skal skille der øyet trenger hjelp, ellers være borte.

| | |
|---|---|
| Kolonneoverskrifter | 8 pt grå kapitéler med sperring, mørk strek under |
| Varelinjer | hårlinje `#D9D9D9` under hver rad, ingen loddrette streker |
| Kundeboks | ingen rammer, bare etiketter i grå kapitéler |
| Sumtabell | høyrestilt, én strek over «Sum inkl. mva», som er 12 pt |
| Logo | gul hårstrek (`#FFCF0D`) under — brukt **én** gang i dokumentet |
| Luft | cellemarger 90 twips topp/bunn i varetabellen |

Farger fra logoen: `#12100C` mørk, `#767676` sekundær. Gul er aksent, ikke
flate.

Databladene følger samme språk (`api/dataark.py`): etiketter i grå kapitéler,
produktnavn 20 pt med gul strek under, pris eks. mva stor og inkl. mva liten
under.

Skriptet som gjorde omformingen ligger som `maler/omform-mal.py`. Det er
**ikke** en del av tjenesten og kjøres ikke automatisk — det er dokumentasjon
av hva som ble endret, og kan kjøres på nytt om malen må bygges opp igjen.
Sikkerhetskopi av malen før omformingen: `maler/tilbud-mal.docx.bak-design-2026-09-01`.

Verifisert at alle docxtpl-markørene overlevde omformingen — inkludert
`{%tr for l in linjer %}` og `{%tr endfor %}`, som er de skjøre.

**Produktdatablad bakerst i dokumentet.** Ett ark per vare etter selve
tilbudet, med sideskift foran: navn, varenr., bilde (65 mm), din pris i
tilbudet, beskrivelse, teknisk informasjon, vaskeanvisning og EAN. Samme vare
to ganger i tilbudet gir ett ark. `api/dataark.py`.

Dette bygges i **kode**, ikke i .docx-malen — i motsetning til resten av
dokumentet. Antall ark og antall avsnitt varierer per tilbud, og felter mangler
ofte: av 439 tilbudsvarer har 430 beskrivelse, 321 teknisk beskrivelse, 164
vaskeanvisning og 37 EAN. En mal måtte da hatt tomme overskrifter stående.
Skriften følger malens egne stiler (Heading 1/2, Normal), så profilendringer i
malen slår likevel gjennom.

Teksten slås opp **nå**, ikke fra frosne data. Bevisst skille: prisen er en
forpliktelse og fryses, en produktbeskrivelse er ikke det — rettes teksten i
butikken, skal en ny utskrift ha den rettede teksten.

Prisen på arket tas fra den frosne linja, med samme etikett som i pristabellen.
Google Apps Scriptet gjorde nettopp ikke det: det skrev `price_incl_tax` under
overskriften «Pris eks. mva», så samme vare sto til to ulike priser i samme
dokument. Ikke gjeninnfør det.

Feiler databladene, logges det og tilbudet blir til likevel — prisene er det
som betyr noe, og et manglende ark er synlig for selgeren.

**HTML-vasking:** `<figure>` fjernes med innhold. Vaskeanvisningene er bygget
som vaskesymbol (`<img>`) pluss `<figcaption>`; uten bildene ble bildetekstene
stående igjen som en løs liste rett etter samme informasjon i løpende tekst.
Merk at container-elementer og void-elementer må behandles hver for seg — en
felles regex med alternativet `/?>` matcher `>` i selve åpningstaggen, så bare
taggen forsvinner og innholdet blir stående. Det var nettopp feilen første
forsøk.

**Både eks. og inkl. mva vises overalt i skjemaet.** Butikken er ren B2C og
viser priser inkl. mva; tilbudet er eks. mva. Selgeren måtte regne i hodet når
kunden refererte til nettbutikkprisen. Nå står inkl.-tallet i grått under
eks.-tallet i søketreff, i Veil.-kolonnen, i Linjesum og på «Sum før rabatt»/Rabatt.

Satsen tas fra produktets `mva_sats`, som nå følger med i `/api/sok`. **Den
manglet før** — nettleseren regnet med en hardkodet 25 %. Serveren regnet
riktig uansett, så lagrede tilbud har alltid vært korrekte, men skjermen ville
løyet for et produkt i en annen tax class. Hardkodet mva er en felle butikken
har gått i to ganger - se «Det viktigste å forstå: mva» under.

**Fallgruve verdt å huske: `textContent` sletter barn.** Inkl.-tallet lå først
som et `<span id="sum-veil-inkl">` INNE i `<td id="sum-veil">`. Da
`oppdaterSummer()` satte `textContent` på td-en, forsvant spanet; neste
oppslag ga `null`, `TypeError`, og resten av funksjonen kjørte aldri. Utslaget
var at rabattraden ble stående på `0,00` mens tallene over var riktige — altså
en feil som så ut som en regnefeil, men var en DOM-feil.

Rettet ved at hver verdi har sitt eget `<span>`, og all skriving går gjennom
`settTekst()`, som hopper over en manglende node i stedet for å kaste. Vernet
blir stående: en summering skal ikke kunne dø av ett manglende element.

**Rabatten er synlig for selgeren.** Sammendraget under varetabellen viser nå
brutto → rabatt → netto, og rabattlinjene dukker bare opp når det faktisk gis
rabatt:

    Sum før rabatt, eks. mva    7 985,40
    Rabatt                   −943,90  (11,82 %)
    herav fra pågående kampanje  −500,00
    Sum eks. mva            7 041,50

Raden heter «Sum før rabatt, eks. mva», ikke «Sum veil.» — med «Sum eks. mva»
rett under ble «veil.» lest som nok et sluttall i stedet for utgangspunktet.

Rabatten måles mot **veil. pris** (listepris), fordi det er tallet som står i
kolonnen ved siden av og det kunden oppfatter som utgangspunkt. Men selgerens
prosent regnes av `kampanjepris` (`api/prising.py`), så går det en kampanje er
avviket fra veil. større enn prosenten selgeren skrev inn. Den differansen
vises for seg som «herav fra pågående kampanje» — ellers ville selgeren tro han
ga bort mer enn han gjorde. Gjelder sjelden: 2 av 80 søketreff har kampanjepris
ulik listepris, men det skal stemme når det først skjer.

**Linjesummen viser nå prisen etter rabatt**, og bare det som tilfører noe.
Stykkprisen vises når antallet er over 1, rabatten når det gis en. Med ett stk.
og ingen rabatt sto det tidligere «1 599,20» og «1 599,20 pr. stk.» under
hverandre — samme tall to ganger.

---

Selger søker opp produkter fra BWW-katalogen, setter rabatt eller fastpris,
og får et Word-dokument på e-post. Selgeren justerer det selv og sender til
kunde. Word og ikke PDF nettopp fordi det siste steget krever redigering.

## Oppsett

| | |
|---|---|
| Kode | `/var/www/tilbud.betterworkwear.no` |
| Tjeneste | `tilbud.service`, bruker `tilbudweb`, uvicorn på `127.0.0.1:8002`, systemd-sandkasse |
| nginx | proxy_pass, TLS via certbot (fornyes automatisk) |
| Database | `tilbud` |
| DB-bruker | `tilbud@localhost` — `ALL` på `tilbud`; `SELECT` på `bww_web.products`, `product_variants`, `product_images` og `categories`. Ingen tilgang til `bbw` (Magento). |
| Git | `origin` = `Serpas-AS/tilbud-betterworkwear` (privat), via SSH-aliaset `github-tilbud` og deploy-nøkkelen `~/.ssh/id_ed25519_tilbud` |
| Hemmeligheter | `.env` (`600 root:root`), lest av systemd |

`python-docx`, `docxtpl`, `mysql-connector-python`, `fastapi`, `uvicorn`,
`jinja2`, `itsdangerous`, `requests`, `pillow`, `lxml`, `urllib3`, `openpyxl` i
`venv/`. Fasit: `requirements.txt`.

## Det viktigste å forstå: mva

BWW lagrer katalogpriser **inkl. 25 % mva** i `bww_web`, og nettbutikken er ren
B2C med priser inkl. mva. Tilbud vises **eks. mva**. `api/katalog.py` regner ned.
`bww_web` har ingen mva-klasser, så satsen er én verdi - men den leses fra
`KATALOG_MVA_SATS` (standard 25) og følger med hvert produkt som `mva_sats`,
den står **ikke** som `/ 1.25` i regnestykkene. Varer utenfor nettbutikken
regnes om med samme sats.

Butikken er brent på hardkodet `/ 1.25` to ganger i Magento-tiden:
`Serpas_CmsProductBox` delte `getFinalPrice()` på 1,25 og skrev «eks. mva» som
ren tekst, så kunder så **799 kr** på en salgsside og ble belastet **999 kr**;
`Serpas_PrebuiltCart` hadde samme mønster. Begge ble fjernet 31.08.2026. Ikke
gjeninnfør mønsteret her: kode som regner mva selv, utenom ett sted med
konfigurert sats, kommer før eller siden i utakt.

**Norsk B2B-salg skal ha 25 % mva.** Bedriftskunden trekker fra inngående mva
selv. Nullsats gjelder eksport og reverse charge, ikke innenlands firmasalg.
At tilbudet vises eks. mva er visning, ikke fritak - summene har alltid mva.

## Fallgruver som allerede er håndtert

**Konfigurerbare produkter har prisen i variantene.** Plaggene med størrelser
er `type = 'configurable'` i `bww_web.products`, og den ekte prisen ligger i
`product_variants`. `api/katalog.py` bruker høyeste aktive variantpris som veil.
pris og laveste som kampanjepris (samme regel som Magento-versjonen). Leser man
bare `products.price`, kan selgeren få 0,00 og gi varen bort.

(Magento-tidens fallgruver - `price = 0` i prisindeksen, rå EAV-lesing og
installasjonsspesifikke attributt-ID-er - utgikk med byttet 03.10.2026.)

**Enten rabatt eller fastpris.** Håndhevet i `api/prising.py`, ikke bare i
skjemaet. Nettleseren er ikke sannhetskilde når det gjelder priser.

**Priser fryses per tilbudslinje.** Et tilbud er en forpliktelse på et
tidspunkt. `tilbud_linje` lagrer listepris, rabatt, nettopris og mva-sats slik
de var. Ingenting slås opp på nytt ved visning eller nedlasting.

**Vern mot gratis varer.** En linje med nettopris 0 avvises med mindre
fastpris er satt eksplisitt.

## Hvorfor `.env` er `600 root:root`

`systemd` leser `EnvironmentFile=` som **root**, før den slipper ned til
`User=tilbudweb`. Fila trenger derfor ikke være lesbar for tjenestebrukeren
i det hele tatt.

Sto den `640 root:www-data`, kunne **alle Magento-butikkenes PHP-FPM** på
serveren lest den — de kjører som `www-data`. Mars–august 2026 ble det funnet
**14 431 filer** med web-shell-kode i `/tmp`, skrevet av `www-data`. Det er en
reell eksponering av databasepassordet og Mailgun-nøkkelen.

Verifisert etter innstrammingen: verken `www-data` eller `magento` kan lese
fila, og tjenesten slår fortsatt opp brukere i databasen.

**Redigering krever `sudo`.** Det er meningen:

    sudo nano /var/www/tilbud.betterworkwear.no/.env
    sudo systemctl restart tilbud.service

## E-post

Mailgun, samme konto og domene som nettbutikken (ny.betterworkwear.no bruker
HTTP-API-et). SMTP-reserven er `smtp.eu.mailgun.org:465` SSL, bruker
`postmaster@betterworkwear.no`.

Bevisst BWWs eget domene og ikke `wrks.no` som puls bruker: domenet er
allerede SPF/DKIM-verifisert, så posten havner ikke i søppelpost, og skal
tilbudet en dag gå rett til kunde står det riktig avsender på det.

**I drift brukes HTTP-API-et, ikke SMTP.** `api/epost.py` støtter begge og
velger API når `MAILGUN_API_KEY` er satt — ett HTTP-kall er raskere enn et
SMTP-håndtrykk med TLS, og feiler tydeligere.

**Det verifiserte Mailgun-domenet er `betterworkwear.no`.** Ikke
`email.betterworkwear.no`, som Magento brukte som avsenderadresse — det
domenet finnes ikke i Mailgun-kontoen (verifisert 31.08.2026, gir 404).
Avsender her er derfor `tilbud@betterworkwear.no`.

Merk at Mailgun har **to ulike legitimasjoner** som ikke er utbyttbare:
API-nøkkel (~50 tegn, hex) og SMTP-passord for `postmaster@`. Legges en
API-nøkkel i `SMTP_PASSWORD`, feiler utsendingen med
«Connection unexpectedly closed» — som ser ut som et nettverksproblem,
men er feil legitimasjon.

Verifisert 31.08.2026: innloggingskode og tilbud med Word-vedlegg
`delivered` i Mailgun.

## Roller

`selger` ser egne tilbud. `ledelse` og `admin` ser alle. Avgjøres i
`api/tjeneste.py`, ikke i rutene — da kan ikke en ny rute glemme sjekken.
Verifisert: en selger får 404 på andres tilbud, også på dokumentnedlasting.

Ingen selvregistrering. Nye brukere legges inn under **/brukere**, som bare
administratorer ser (menylenka vises heller ikke for andre).

**Brukere slettes ikke, de deaktiveres.** Ellers ville tilbudene deres mistet
avsenderen sin — `tilbud.user_id` har fremmednøkkel mot `users`.

**Sperre mot utestenging:** den siste aktive administratoren kan verken
fratas rollen eller deaktiveres. Uten den kunne et feilklikk gjort at ingen
lenger kan legge til brukere, og da måtte man inn i databasen for å komme ut
av det. Verifisert: begge forsøk avvises.

## Status / sendt

Systemet vet at et tilbud er **generert**. Det kan ikke vite at det er
**sendt**, siden selgeren sender fra egen e-post. Derfor «Marker som sendt».
Uten det ville ledelsens tall vært høyere enn virkeligheten.

## Utestående

- Malen `maler/tilbud-mal.docx` er funksjonell, men uten logo og profil.
  Åpne den i Word og pynt den; så lenge `{{ }}`-feltene og de to
  markørradene i varetabellen står igjen, virker generatoren.
