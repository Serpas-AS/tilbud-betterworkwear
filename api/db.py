"""
Databasetilkoblinger.

To adskilte tilkoblinger med vilje:
  tilbud_db()  - egen base, full skrivetilgang
  katalog_db() - nettbutikkens katalog (bww_web), KUN lesetilgang

MySQL-brukeren `tilbud` har bare `SELECT` på de tre katalogtabellene i bww_web
(products, product_variants, product_images) - ikke på ordrer, kunder eller
admin-tabellene der. Et skrivekall mot katalogen feiler med ERROR 1142 uansett
hva koden her skulle finne på.
Det er en bevisst siste skanse - ikke fjern den ved å gi brukeren mer.
"""
import os
import mysql.connector

_SOCKET = os.environ["TILBUD_DB_SOCKET"]
_USER = os.environ["TILBUD_DB_USER"]
_PASSWORD = os.environ["TILBUD_DB_PASSWORD"]


def _connect(database: str):
    return mysql.connector.connect(
        unix_socket=_SOCKET,
        user=_USER,
        password=_PASSWORD,
        database=database,
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=True,
    )


def tilbud_db():
    return _connect(os.environ["TILBUD_DB_NAME"])


def katalog_db():
    return _connect(os.environ.get("KATALOG_DB_NAME", "bww_web"))
