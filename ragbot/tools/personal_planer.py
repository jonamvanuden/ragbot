"""Mock Personal-Planer API: 11 tools across the product modules.

Nothing here talks to a real system: all data lives in the mock ERP database data/erp.db
(rebuild with: python -m ragbot.erp.seed). Read tools query it, write tools check the
business rules (remaining vacation, overlaps, working-time law …) and then change it.
Every call is also logged in chat.db (api_calls) as the audit log.
"""
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ragbot.erp import rules
from ragbot.erp.db import connect
from ragbot.erp.seed import workdays
from ragbot.tools.registry import Context, ToolError, tool

# ---------------- data comes from the mock ERP database (data/erp.db) ----------------
def _rows(sql: str, *args) -> list[dict]:
    """Run a SELECT on erp.db and return the rows as dicts."""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _write(sql: str, *args):
    """Run one INSERT/UPDATE on erp.db and commit it."""
    conn = connect()
    try:
        with conn:
            conn.execute(sql, args)
    finally:
        conn.close()


def _next_id(table: str, prefix: str) -> str:
    """Next free ID like UA-00127: highest number in the table + 1."""
    n = _rows(f"SELECT MAX(CAST(SUBSTR(id, {len(prefix) + 2}) AS INTEGER)) AS n FROM {table}")[0]["n"] or 0
    return f"{prefix}-{n + 1:05d}"


MitarbeiterId = Field(description="Mitarbeiter-ID im Format MA-1234. Unbekannt? Erst mitarbeiter_suchen aufrufen.",
                      pattern=r"^MA-\d{4}$")

PERSON_SQL = """
    SELECT m.id, m.vorname || ' ' || m.nachname AS name, a.name AS abteilung, m.rolle,
           m.vorgesetzt_id AS vorgesetzt, m.urlaubstage, m.wochenstunden, m.email, m.eintritt
    FROM mitarbeiter m JOIN abteilungen a ON a.id = m.abteilung_id
    WHERE m.aktiv = 1
"""


def _person(mid: str) -> dict:
    rows = _rows(PERSON_SQL + " AND m.id = ?", mid)
    if not rows:
        raise ToolError(f"Mitarbeiter {mid} existiert nicht. Mit mitarbeiter_suchen die richtige ID finden.")
    return rows[0]


def _workdays(start: date, end: date) -> int:
    """Mon–Fri between start and end (inclusive), without public holidays (same rule as the seed)."""
    return len(workdays(start, end))


def _vacations() -> list[dict]:
    """All vacation requests (except cancelled ones), with name and department."""
    return _rows("""
        SELECT u.id AS antrag_id, u.mitarbeiter_id, m.vorname || ' ' || m.nachname AS name, a.name AS abteilung,
               u.von, u.bis, u.arbeitstage, u.art, u.status
        FROM urlaubsantraege u
        JOIN mitarbeiter m ON m.id = u.mitarbeiter_id
        JOIN abteilungen a ON a.id = m.abteilung_id
        WHERE u.status != 'storniert'
        ORDER BY u.von""")


# =====================================================================
# Digitale Personalakte
# =====================================================================
class MitarbeiterSuchenParams(BaseModel):
    suchbegriff: str | None = Field(None, description="Teil des Namens, z. B. 'Weber' oder 'Anna'. Weglassen für alle Mitarbeiter.")
    abteilung: str | None = Field(None, description="z. B. Vertrieb, Entwicklung, Support, IT-Betrieb")


@tool(MitarbeiterSuchenParams, product="Digitale Personalakte", kind="read")
def mitarbeiter_suchen(p: MitarbeiterSuchenParams, ctx: Context) -> dict:
    """Sucht Mitarbeiter nach Name und/oder Abteilung und liefert ID, Rolle und Vorgesetzte.
    Ohne Parameter liefert es ALLE Mitarbeiter (für Listen und Übersichten).
    Immer zuerst verwenden, wenn ein Name statt einer Mitarbeiter-ID genannt wird."""
    sql, args = PERSON_SQL, []
    if p.suchbegriff:
        sql += " AND (m.vorname || ' ' || m.nachname) LIKE ?"
        args.append(f"%{p.suchbegriff}%")
    if p.abteilung:
        sql += " AND a.name LIKE ?"
        args.append(p.abteilung)
    hits = _rows(sql + " ORDER BY m.nachname", *args)
    return {"anzahl": len(hits),
            "mitarbeiter": [{k: m[k] for k in ("id", "name", "abteilung", "rolle", "vorgesetzt")} for m in hits]}


# =====================================================================
# Urlaubsplaner
# =====================================================================
class UrlaubBeantragenParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    von: date = Field(description="Erster Urlaubstag (YYYY-MM-DD)")
    bis: date = Field(description="Letzter Urlaubstag (YYYY-MM-DD)")
    art: Literal["Erholungsurlaub", "Sonderurlaub", "Unbezahlter Urlaub"] = "Erholungsurlaub"
    kommentar: str | None = Field(None, description="Optionale Notiz für die/den Vorgesetzte(n)")


@tool(UrlaubBeantragenParams, product="Urlaubsplaner", kind="write")
def urlaub_beantragen(p: UrlaubBeantragenParams, ctx: Context) -> dict:
    """Stellt einen Urlaubsantrag für einen Mitarbeiter. Der Antrag hat danach den Status
    'beantragt' und muss von der/dem Vorgesetzten genehmigt werden."""
    m = _person(p.mitarbeiter_id)
    if p.bis < p.von:
        raise ToolError("Das Enddatum liegt vor dem Startdatum.")
    tage = _workdays(p.von, p.bis)
    if tage == 0:
        raise ToolError("Der Zeitraum enthält keinen Arbeitstag (nur Wochenende).")
    if p.art == "Erholungsurlaub":
        rest = urlaubsanspruch_rest(m["id"], p.von.year)
        if tage > rest:
            raise ToolError(f"Nicht genug Resturlaub: {tage} Tage beantragt, nur noch {rest} verfügbar.")
    overlap = next((v for v in _vacations() if v["mitarbeiter_id"] == m["id"]
                    and v["status"] in ("beantragt", "genehmigt")
                    and v["von"] <= p.bis.isoformat() and v["bis"] >= p.von.isoformat()), None)
    if overlap:
        raise ToolError(f"Überschneidung mit Antrag {overlap['antrag_id']} "
                        f"({overlap['von']} bis {overlap['bis']}, {overlap['status']}).")

    antrag_id = _next_id("urlaubsantraege", "UA")
    _write("""INSERT INTO urlaubsantraege (id, mitarbeiter_id, von, bis, arbeitstage, art, status, kommentar, beantragt_am)
              VALUES (?, ?, ?, ?, ?, ?, 'beantragt', ?, ?)""",
           antrag_id, m["id"], p.von.isoformat(), p.bis.isoformat(), tage, p.art, p.kommentar, date.today().isoformat())
    return {"antrag_id": antrag_id, "mitarbeiter_id": m["id"], "name": m["name"],
            "abteilung": m["abteilung"], "von": p.von.isoformat(), "bis": p.bis.isoformat(),
            "arbeitstage": tage, "art": p.art, "status": "beantragt", "genehmigung_durch": m["vorgesetzt"]}


def urlaubsanspruch_rest(mitarbeiter_id: str, jahr: int) -> int:
    used = sum(v["arbeitstage"] for v in _vacations()
               if v["mitarbeiter_id"] == mitarbeiter_id and v["von"].startswith(str(jahr))
               and v["art"] == "Erholungsurlaub" and v["status"] != "abgelehnt")
    return _person(mitarbeiter_id)["urlaubstage"] - used


class UrlaubsanspruchParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    jahr: int = Field(default_factory=lambda: date.today().year, ge=2020, le=2100)


@tool(UrlaubsanspruchParams, product="Urlaubsplaner", kind="read")
def urlaubsanspruch_abfragen(p: UrlaubsanspruchParams, ctx: Context) -> dict:
    """Zeigt Jahresanspruch, genommene/geplante Tage und Resturlaub eines Mitarbeiters."""
    m = _person(p.mitarbeiter_id)
    mine = [v for v in _vacations() if v["mitarbeiter_id"] == m["id"] and v["von"].startswith(str(p.jahr))
            and v["art"] == "Erholungsurlaub"]
    genehmigt = sum(v["arbeitstage"] for v in mine if v["status"] == "genehmigt")
    offen = sum(v["arbeitstage"] for v in mine if v["status"] == "beantragt")
    return {"mitarbeiter_id": m["id"], "name": m["name"], "jahr": p.jahr, "anspruch": m["urlaubstage"],
            "genehmigt": genehmigt, "beantragt_offen": offen, "rest": m["urlaubstage"] - genehmigt - offen,
            "antraege": [{k: v[k] for k in ("antrag_id", "von", "bis", "arbeitstage", "status")} for v in mine]}


class UrlaubsantragEntscheidenParams(BaseModel):
    antrag_id: str = Field(description="Antragsnummer im Format UA-00042", pattern=r"^UA-\d{5}$")
    entscheidung: Literal["genehmigt", "abgelehnt"]
    begruendung: str | None = Field(None, description="Pflicht bei Ablehnung")


@tool(UrlaubsantragEntscheidenParams, product="Urlaubsplaner", kind="write")
def urlaubsantrag_entscheiden(p: UrlaubsantragEntscheidenParams, ctx: Context) -> dict:
    """Genehmigt oder lehnt einen offenen Urlaubsantrag ab (Rolle: Vorgesetzte)."""
    antrag = next((v for v in _vacations() if v["antrag_id"] == p.antrag_id), None)
    if not antrag:
        raise ToolError(f"Antrag {p.antrag_id} nicht gefunden.")
    if antrag["status"] != "beantragt":
        raise ToolError(f"Antrag {p.antrag_id} ist bereits {antrag['status']}.")
    if p.entscheidung == "abgelehnt" and not p.begruendung:
        raise ToolError("Eine Ablehnung braucht eine Begründung.")
    _write("UPDATE urlaubsantraege SET status = ?, entschieden_am = ?, begruendung = ? WHERE id = ?",
           p.entscheidung, date.today().isoformat(), p.begruendung, p.antrag_id)
    return {"antrag_id": p.antrag_id, "name": antrag["name"], "von": antrag["von"], "bis": antrag["bis"],
            "status": p.entscheidung, "begruendung": p.begruendung, "benachrichtigt": antrag["mitarbeiter_id"]}


class AbwesenheitenParams(BaseModel):
    von: date = Field(description="Beginn des Zeitraums (YYYY-MM-DD)")
    bis: date = Field(description="Ende des Zeitraums (YYYY-MM-DD)")
    abteilung: str | None = Field(None, description="Optional nur eine Abteilung")


@tool(AbwesenheitenParams, product="Urlaubsplaner", kind="read")
def abwesenheiten_abfragen(p: AbwesenheitenParams, ctx: Context) -> dict:
    """Listet, wer in einem Zeitraum abwesend ist (beantragte und genehmigte Urlaube)."""
    if p.bis < p.von:
        raise ToolError("Das Enddatum liegt vor dem Startdatum.")
    hits = [v for v in _vacations()
            if v["status"] != "abgelehnt"
            and date.fromisoformat(v["von"]) <= p.bis and date.fromisoformat(v["bis"]) >= p.von
            and (not p.abteilung or v["abteilung"].lower() == p.abteilung.lower())]
    return {"zeitraum": f"{p.von} bis {p.bis}", "anzahl": len(hits),
            "abwesenheiten": [{k: v[k] for k in ("name", "abteilung", "von", "bis", "art", "status")} for v in hits]}


# =====================================================================
# Arbeitszeitverwaltung
# =====================================================================
class ArbeitszeitBuchenParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    datum: date
    beginn: time = Field(description="z. B. 08:00")
    ende: time = Field(description="z. B. 16:30")
    pause_minuten: int = Field(30, ge=0, le=240)


@tool(ArbeitszeitBuchenParams, product="Arbeitszeitverwaltung", kind="write")
def arbeitszeit_buchen(p: ArbeitszeitBuchenParams, ctx: Context) -> dict:
    """Bucht eine Arbeitszeit (Kommen/Gehen) nachträglich oder manuell für einen Tag."""
    m = _person(p.mitarbeiter_id)
    if p.ende <= p.beginn:
        raise ToolError("Ende muss nach Beginn liegen (Nachtschichten bitte in zwei Buchungen).")
    minuten = int((datetime.combine(p.datum, p.ende) - datetime.combine(p.datum, p.beginn)).total_seconds() // 60) - p.pause_minuten
    if minuten > 10 * 60:
        raise ToolError("Mehr als 10 Stunden Arbeitszeit pro Tag sind laut ArbZG nicht zulässig.")
    if minuten > 6 * 60 and p.pause_minuten < 30:
        raise ToolError("Ab 6 Stunden sind mindestens 30 Minuten Pause vorgeschrieben.")
    if _rows("SELECT 1 FROM zeitbuchungen WHERE mitarbeiter_id = ? AND datum = ?", m["id"], p.datum.isoformat()):
        raise ToolError(f"Für {m['name']} gibt es am {p.datum} bereits eine Zeitbuchung.")
    _write("""INSERT INTO zeitbuchungen (mitarbeiter_id, datum, beginn, ende, pause_minuten, netto_stunden, quelle)
              VALUES (?, ?, ?, ?, ?, ?, 'manuell')""",
           m["id"], p.datum.isoformat(), p.beginn.strftime("%H:%M"), p.ende.strftime("%H:%M"),
           p.pause_minuten, round(minuten / 60, 2))
    return {"mitarbeiter_id": m["id"], "name": m["name"],
            "datum": p.datum.isoformat(), "beginn": p.beginn.strftime("%H:%M"), "ende": p.ende.strftime("%H:%M"),
            "pause_minuten": p.pause_minuten, "netto_stunden": round(minuten / 60, 2), "quelle": "manuell"}


class ZeitkontoParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    monat: str = Field(default_factory=lambda: date.today().strftime("%Y-%m"),
                       description="Monat im Format YYYY-MM", pattern=r"^\d{4}-\d{2}$")


@tool(ZeitkontoParams, product="Arbeitszeitverwaltung", kind="read")
def zeitkonto_abfragen(p: ZeitkontoParams, ctx: Context) -> dict:
    """Zeigt Soll-, Ist-Stunden und Saldo (Überstunden) eines Mitarbeiters für einen Monat.
    Gezählt werden die bereits vergangenen Arbeitstage; Urlaubs- und Krankheitstage zählen nicht zum Soll."""
    m = _person(p.mitarbeiter_id)
    k = rules.zeitkonto(_rows, m["id"], m["wochenstunden"], p.monat)
    if k["zeitraum"] is None:
        raise ToolError(f"Für {p.monat} gibt es noch keine abgeschlossenen Arbeitstage.")
    return {"mitarbeiter_id": m["id"], "name": m["name"], **k}


# =====================================================================
# Personaleinsatzplaner
# =====================================================================
class SchichtZuweisenParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    datum: date
    schicht: Literal["Frühschicht", "Spätschicht", "Nachtschicht"]
    standort: str = Field("Euskirchen", description="Einsatzort")


@tool(SchichtZuweisenParams, product="Personaleinsatzplaner", kind="write")
def schicht_zuweisen(p: SchichtZuweisenParams, ctx: Context) -> dict:
    """Plant einen Mitarbeiter für eine Schicht an einem Tag ein. Prüft Urlaubskonflikte."""
    m = _person(p.mitarbeiter_id)
    clash = next((v for v in _vacations() if v["mitarbeiter_id"] == m["id"] and v["status"] != "abgelehnt"
                  and v["von"] <= p.datum.isoformat() <= v["bis"]), None)
    if clash:
        raise ToolError(f"{m['name']} hat am {p.datum} Urlaub ({clash['antrag_id']}, {clash['status']}).")
    typ = p.schicht.removesuffix("schicht")                        # "Frühschicht" → "Früh" (as stored in the DB)
    if _rows("SELECT 1 FROM schichten WHERE mitarbeiter_id = ? AND datum = ?", m["id"], p.datum.isoformat()):
        raise ToolError(f"{m['name']} ist am {p.datum} bereits für eine Schicht eingeplant.")
    _write("INSERT INTO schichten (datum, typ, mitarbeiter_id, standort) VALUES (?, ?, ?, ?)",
           p.datum.isoformat(), typ, m["id"], p.standort)
    zeiten = {"Frühschicht": "06:00–14:00", "Spätschicht": "14:00–22:00", "Nachtschicht": "22:00–06:00"}
    return {"mitarbeiter_id": m["id"], "name": m["name"], "datum": p.datum.isoformat(),
            "schicht": p.schicht, "zeiten": zeiten[p.schicht], "standort": p.standort}


# =====================================================================
# Besucherverwaltung
# =====================================================================
class BesucherAnmeldenParams(BaseModel):
    name: str = Field(min_length=2, description="Vor- und Nachname des Besuchers")
    firma: str | None = None
    datum: date
    uhrzeit: time = Field(description="Erwartete Ankunft, z. B. 10:30")
    gastgeber_id: str = Field(description="Mitarbeiter-ID der Person, die den Besuch empfängt", pattern=r"^MA-\d{4}$")
    zweck: str | None = Field(None, description="z. B. Kundentermin, Vorstellungsgespräch, Wartung")


@tool(BesucherAnmeldenParams, product="Besucherverwaltung", kind="write")
def besucher_anmelden(p: BesucherAnmeldenParams, ctx: Context) -> dict:
    """Meldet einen Besucher vorab an. Der Gastgeber wird benachrichtigt, der Empfang bekommt einen Ausweis."""
    host = _person(p.gastgeber_id)
    if p.datum < date.today():
        raise ToolError("Besuche können nicht in der Vergangenheit angemeldet werden.")
    besuch_id = _next_id("besucher", "BS")
    _write("""INSERT INTO besucher (id, name, firma, gastgeber_id, datum, uhrzeit, status)
              VALUES (?, ?, ?, ?, ?, ?, 'angemeldet')""",
           besuch_id, p.name, p.firma, host["id"], p.datum.isoformat(), p.uhrzeit.strftime("%H:%M"))
    return {"besuch_id": besuch_id, "besucher": p.name, "firma": p.firma,
            "datum": p.datum.isoformat(), "uhrzeit": p.uhrzeit.strftime("%H:%M"),
            "gastgeber": host["name"], "zweck": p.zweck, "status": "angemeldet", "benachrichtigt": host["id"]}


# =====================================================================
# Aufgabenmanagement
# =====================================================================
class AufgabeErstellenParams(BaseModel):
    titel: str = Field(min_length=3)
    beschreibung: str | None = None
    zustaendig_id: str = Field(description="Mitarbeiter-ID der zuständigen Person", pattern=r"^MA-\d{4}$")
    faellig_am: date | None = None
    prioritaet: Literal["niedrig", "normal", "hoch"] = "normal"


@tool(AufgabeErstellenParams, product="Aufgabenmanagement", kind="write")
def aufgabe_erstellen(p: AufgabeErstellenParams, ctx: Context) -> dict:
    """Legt eine Aufgabe an und weist sie einem Mitarbeiter zu."""
    m = _person(p.zustaendig_id)
    aufgabe_id = _next_id("aufgaben", "AU")
    _write("""INSERT INTO aufgaben (id, titel, beschreibung, zugewiesen_id, faellig, prioritaet, status)
              VALUES (?, ?, ?, ?, ?, ?, 'offen')""",
           aufgabe_id, p.titel, p.beschreibung, m["id"],
           p.faellig_am.isoformat() if p.faellig_am else None, p.prioritaet)
    return {"aufgabe_id": aufgabe_id, "titel": p.titel, "zustaendig": m["name"],
            "faellig_am": p.faellig_am.isoformat() if p.faellig_am else None,
            "prioritaet": p.prioritaet, "status": "offen"}


# =====================================================================
# Digitales Postbuch
# =====================================================================
class PostErfassenParams(BaseModel):
    absender: str = Field(min_length=2)
    empfaenger_id: str = Field(description="Mitarbeiter-ID des Empfängers", pattern=r"^MA-\d{4}$")
    betreff: str
    art: Literal["Brief", "Einschreiben", "Paket", "Fax", "E-Mail-Ausdruck"] = "Brief"
    eingang_am: date = Field(default_factory=date.today)


@tool(PostErfassenParams, product="Digitales Postbuch", kind="write")
def post_erfassen(p: PostErfassenParams, ctx: Context) -> dict:
    """Erfasst einen Posteingang im digitalen Postbuch und benachrichtigt den Empfänger."""
    m = _person(p.empfaenger_id)
    post_id = _next_id("post", "PB")
    _write("INSERT INTO post (id, absender, empfaenger_id, betreff, art, eingang_am) VALUES (?, ?, ?, ?, ?, ?)",
           post_id, p.absender, m["id"], p.betreff, p.art, p.eingang_am.isoformat())
    return {"post_id": post_id, "absender": p.absender, "empfaenger": m["name"],
            "betreff": p.betreff, "art": p.art, "eingang_am": p.eingang_am.isoformat(),
            "fristrelevant": p.art == "Einschreiben", "benachrichtigt": m["id"]}