"""
Prisberegning for en tilbudslinje.

Alle belop her er EKS mva. Katalogen leverer allerede eks. mva fra
api/katalog.py, som regner ned fra katalogens inkl.-mva-priser.

Reglene, avklart 31.08.2026:

  * Rabatt ELLER fastpris - aldri begge. Handheves her, uavhengig av hva
    skjemaet gjor. Nettleseren er ikke sannhetskilde nar det gjelder priser.
  * Rabatten regnes av `kampanjepris` (dagens faktiske pris), ikke av
    listeprisen. Ellers gir man rabatt oppa en kampanje uten a se det.
  * Avrunding per linje til to desimaler, deretter summeres linjene.
    Da stemmer alltid totalen med linjene i dokumentet.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional


class PrisFeil(ValueError):
    """Ugyldig prisinput. Skal vises til selgeren, ikke svelges."""


def q2(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def beregn_linje(
    produkt: Dict,
    antall: int,
    rabatt_type: str = "ingen",
    rabatt_prosent: Optional[Decimal] = None,
    fastpris: Optional[Decimal] = None,
) -> Dict:
    """
    Returnerer en ferdig frosset tilbudslinje.

    Alt som pavirker prisen lagres i returverdien. Ingenting slas opp pa nytt
    senere - et tilbud er en forpliktelse pa et tidspunkt, og katalogen endrer
    seg. Se tilbud_linje-tabellen.
    """
    if antall is None or int(antall) < 1:
        raise PrisFeil("Antall må være minst 1.")
    antall = int(antall)

    if rabatt_type not in ("ingen", "prosent", "fastpris"):
        raise PrisFeil(f"Ukjent rabatt-type: {rabatt_type}")

    # Enten/eller - handhevet serverside.
    if rabatt_prosent is not None and fastpris is not None:
        raise PrisFeil("Sett enten rabatt i prosent eller fastpris, ikke begge.")

    listepris = q2(produkt["listepris"])
    kampanjepris = q2(produkt["kampanjepris"])
    mva_sats = Decimal(str(produkt["mva_sats"]))

    if rabatt_type == "prosent":
        if rabatt_prosent is None:
            raise PrisFeil("Rabatt i prosent mangler.")
        rabatt_prosent = Decimal(str(rabatt_prosent))
        if not (Decimal("0") <= rabatt_prosent <= Decimal("100")):
            raise PrisFeil("Rabatt må være mellom 0 og 100 %.")
        nettopris = q2(kampanjepris * (Decimal("100") - rabatt_prosent) / Decimal("100"))
        fastpris = None

    elif rabatt_type == "fastpris":
        if fastpris is None:
            raise PrisFeil("Fastpris mangler.")
        fastpris = q2(fastpris)
        if fastpris < 0:
            raise PrisFeil("Fastpris kan ikke være negativ.")
        nettopris = fastpris
        rabatt_prosent = None

    else:
        nettopris = kampanjepris
        rabatt_prosent = None
        fastpris = None

    # Vern mot at et konfigurerbart produkt uten variantpris, eller et hull i
    # indeksen, sender ut et tilbud der varen er gratis. Skal fastprisen
    # virkelig vaere 0, ma det settes eksplisitt.
    if nettopris <= 0 and rabatt_type != "fastpris":
        raise PrisFeil(
            f"{produkt['sku']} har ingen gyldig pris ({nettopris}). "
            "Sett fastpris eksplisitt hvis varen skal med."
        )

    return {
        "produkt_id": produkt["produkt_id"],
        "sku": produkt["sku"],
        "navn": produkt["navn"],
        # Bildet fryses av samme grunn som prisen: et tilbud skal kunne
        # gjenskapes slik kunden fikk det, ogsaa etter at katalogen er endret.
        "bilde": produkt.get("bilde"),
        "antall": antall,
        "listepris": listepris,
        "kampanjepris": kampanjepris,
        "rabatt_type": rabatt_type,
        "rabatt_prosent": rabatt_prosent,
        "fastpris": fastpris,
        "nettopris": nettopris,
        "linjesum": q2(nettopris * antall),
        "mva_sats": mva_sats,
    }


def summer(linjer) -> Dict[str, Decimal]:
    """
    Summerer ferdig avrundede linjer. Mva regnes per mva-sats, ikke pa
    totalen under ett - butikken har i dag bare 25 %, men en vare med annen
    sats ville ellers gitt feil mva-belop.
    """
    sum_eks = Decimal("0")
    sum_mva = Decimal("0")
    for l in linjer:
        sum_eks += l["linjesum"]
        sum_mva += q2(l["linjesum"] * Decimal(str(l["mva_sats"])) / Decimal("100"))
    return {
        "sum_eks_mva": q2(sum_eks),
        "sum_mva": q2(sum_mva),
        "sum_inkl_mva": q2(sum_eks + sum_mva),
    }
