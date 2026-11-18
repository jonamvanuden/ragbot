"""The mock ERP database (SQLite): the 'customer system' the tools and the SDK talk to.

Tables:
  abteilungen       departments, with their manager
  mitarbeiter       employees (contract data, manager, vacation entitlement)
  urlaubsantraege   vacation requests and their decisions
  krankmeldungen    sick leave
  zeitbuchungen     clock-in/clock-out per day
  schichten         shift plan
  besucher          announced visitors
  aufgaben          tasks
  post              incoming mail (digital mail log)

The logged tool calls stay in chat.db (api_calls) – that's the audit log, not ERP data.
"""
import sqlite3

from ragbot import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS abteilungen (
    id          TEXT PRIMARY KEY,                 -- ABT-01
    name        TEXT NOT NULL UNIQUE,
    leitung_id  TEXT REFERENCES mitarbeiter(id)
);

CREATE TABLE IF NOT EXISTS mitarbeiter (
    id             TEXT PRIMARY KEY,              -- MA-1001
    vorname        TEXT NOT NULL,
    nachname       TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    abteilung_id   TEXT NOT NULL REFERENCES abteilungen(id),
    rolle          TEXT NOT NULL,
    vorgesetzt_id  TEXT REFERENCES mitarbeiter(id),
    eintritt       TEXT NOT NULL,                 -- YYYY-MM-DD
    wochenstunden  REAL NOT NULL,
    urlaubstage    INTEGER NOT NULL,
    aktiv          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS urlaubsantraege (
    id               TEXT PRIMARY KEY,            -- UA-00001
    mitarbeiter_id   TEXT NOT NULL REFERENCES mitarbeiter(id),
    von              TEXT NOT NULL,
    bis              TEXT NOT NULL,
    arbeitstage      INTEGER NOT NULL,
    art              TEXT NOT NULL CHECK (art IN ('Erholungsurlaub', 'Sonderurlaub', 'Unbezahlter Urlaub')),
    status           TEXT NOT NULL CHECK (status IN ('beantragt', 'genehmigt', 'abgelehnt', 'storniert')),
    kommentar        TEXT,
    beantragt_am     TEXT NOT NULL,
    entschieden_von  TEXT REFERENCES mitarbeiter(id),
    entschieden_am   TEXT,
    begruendung      TEXT
);

CREATE TABLE IF NOT EXISTS krankmeldungen (
    id               TEXT PRIMARY KEY,            -- KM-00001
    mitarbeiter_id   TEXT NOT NULL REFERENCES mitarbeiter(id),
    von              TEXT NOT NULL,
    bis              TEXT NOT NULL,
    arbeitstage      INTEGER NOT NULL,
    au_bescheinigung INTEGER NOT NULL DEFAULT 0   -- doctor's note yes/no
);

CREATE TABLE IF NOT EXISTS zeitbuchungen (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mitarbeiter_id  TEXT NOT NULL REFERENCES mitarbeiter(id),
    datum           TEXT NOT NULL,
    beginn          TEXT NOT NULL,                -- HH:MM
    ende            TEXT NOT NULL,
    pause_minuten   INTEGER NOT NULL DEFAULT 0,
    netto_stunden   REAL NOT NULL,
    quelle          TEXT NOT NULL CHECK (quelle IN ('terminal', 'manuell'))
);

CREATE TABLE IF NOT EXISTS schichten (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datum           TEXT NOT NULL,
    typ             TEXT NOT NULL CHECK (typ IN ('Früh', 'Spät', 'Nacht')),
    mitarbeiter_id  TEXT NOT NULL REFERENCES mitarbeiter(id),
    standort        TEXT NOT NULL,
    UNIQUE (datum, typ, mitarbeiter_id)
);

CREATE TABLE IF NOT EXISTS besucher (
    id            TEXT PRIMARY KEY,               -- BS-00001
    name          TEXT NOT NULL,
    firma         TEXT,
    gastgeber_id  TEXT NOT NULL REFERENCES mitarbeiter(id),
    datum         TEXT NOT NULL,
    uhrzeit       TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('angemeldet', 'eingetroffen', 'abgesagt'))
);

CREATE TABLE IF NOT EXISTS aufgaben (
    id              TEXT PRIMARY KEY,             -- AU-00001
    titel           TEXT NOT NULL,
    beschreibung    TEXT,
    zugewiesen_id   TEXT NOT NULL REFERENCES mitarbeiter(id),
    erstellt_von    TEXT REFERENCES mitarbeiter(id),
    faellig         TEXT,
    prioritaet      TEXT NOT NULL CHECK (prioritaet IN ('niedrig', 'normal', 'hoch')),
    status          TEXT NOT NULL CHECK (status IN ('offen', 'in Arbeit', 'erledigt'))
);

CREATE TABLE IF NOT EXISTS post (
    id             TEXT PRIMARY KEY,              -- PB-00001
    absender       TEXT NOT NULL,
    empfaenger_id  TEXT NOT NULL REFERENCES mitarbeiter(id),
    betreff        TEXT NOT NULL,
    art            TEXT NOT NULL CHECK (art IN ('Brief', 'Einschreiben', 'Paket', 'Fax', 'E-Mail-Ausdruck')),
    eingang_am     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_urlaub_ma   ON urlaubsantraege(mitarbeiter_id, von);
CREATE INDEX IF NOT EXISTS ix_krank_ma    ON krankmeldungen(mitarbeiter_id, von);
CREATE INDEX IF NOT EXISTS ix_zeit_ma     ON zeitbuchungen(mitarbeiter_id, datum);
CREATE INDEX IF NOT EXISTS ix_schicht_tag ON schichten(datum);
"""


def connect() -> sqlite3.Connection:
    """Open erp.db (creates file + tables on first use). Rows behave like dicts: row["name"]."""
    config.ERP_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.ERP_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn