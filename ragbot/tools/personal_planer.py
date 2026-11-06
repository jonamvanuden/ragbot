"""Mock Personal-Planer API: 11 tools across the product modules.

Nothing here talks to a real system. Write tools (kind="write") only record the call
and return what the real API would plausibly answer. Read tools answer from a small
fixed staff list plus the earlier calls in api_calls, so the mock behaves consistently:
a vacation you requested shows up in the balance, in absences, and can be approved.
"""
import hashlib
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ragbot.tools import store
from ragbot.tools.registry import Context, ToolError, tool

# ---------------- fixed mock data (fictional people) ----------------
MITARBEITER = [
    {"id": "MA-1001", "name": "Anna Schneider",  "abteilung": "Vertrieb",    "rolle": "Key Account Managerin",  "vorgesetzt": "MA-1005", "urlaubstage": 30, "wochenstunden": 40},
    {"id": "MA-1002", "name": "Jonas Weber",     "abteilung": "Entwicklung", "rolle": "PHP-Entwickler",         "vorgesetzt": "MA-1006", "urlaubstage": 28, "wochenstunden": 40},
    {"id": "MA-1003", "name": "Leonie Fischer",  "abteilung": "Support",     "rolle": "Support-Mitarbeiterin",  "vorgesetzt": "MA-1007", "urlaubstage": 30, "wochenstunden": 30},
    {"id": "MA-1004", "name": "Mehmet Yilmaz",   "abteilung": "IT-Betrieb",  "rolle": "Systemadministrator",    "vorgesetzt": "MA-1006", "urlaubstage": 30, "wochenstunden": 40},
    {"id": "MA-1005", "name": "Claudia Becker",  "abteilung": "Vertrieb",    "rolle": "Vertriebsleiterin",      "vorgesetzt": None,      "urlaubstage": 30, "wochenstunden": 40},
    {"id": "MA-1006", "name": "Thomas Wagner",   "abteilung": "Entwicklung", "rolle": "Teamleiter Entwicklung", "vorgesetzt": None,      "urlaubstage": 30, "wochenstunden": 40},
    {"id": "MA-1007", "name": "Sophie Hoffmann", "abteilung": "Support",     "rolle": "Teamleiterin Support",   "vorgesetzt": None,      "urlaubstage": 30, "wochenstunden": 40},
    {"id": "MA-1008", "name": "Lukas Schäfer",   "abteilung": "Support",     "rolle": "Werkstudent",            "vorgesetzt": "MA-1007", "urlaubstage": 15, "wochenstunden": 20},
]
BY_ID = {m["id"]: m for m in MITARBEITER}

MitarbeiterId = Field(description="Mitarbeiter-ID im Format MA-1234. Unbekannt? Erst mitarbeiter_suchen aufrufen.",
                      pattern=r"^MA-\d{4}$")


def _person(mid: str) -> dict:
    if mid not in BY_ID:
        raise ToolError(f"Mitarbeiter {mid} existiert nicht. Mit mitarbeiter_suchen die richtige ID finden.")
    return BY_ID[mid]


def _workdays(start: date, end: date) -> int:
    """Mon–Fri between start and end (inclusive). Public holidays are ignored in the mock."""
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)


def _vacations() -> list[dict]:
    """All vacation requests so far, with their current decision."""
    decisions = {c["params"]["antrag_id"]: c["params"]["entscheidung"] for c in store.successful("urlaubsantrag_entscheiden")}
    out = []
    for c in store.successful("urlaub_beantragen"):
        r = c["result"]
        out.append({**r, "status": decisions.get(r["antrag_id"], "beantragt")})
    return out


def _stable_offset(key: str, lo: float, hi: float) -> float:
    """Deterministic pseudo-random number, so the same question gives the same mock answer."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return round((lo + (hi - lo) * h) * 4) / 4          # quarter hours


# =====================================================================
# Digitale Personalakte
# =====================================================================
class MitarbeiterSuchenParams(BaseModel):
    suchbegriff: str | None = Field(None, description="Teil des Namens, z. B. 'Weber' oder 'Anna'.")
    abteilung: str | None = Field(None, description="z. B. Vertrieb, Entwicklung, Support, IT-Betrieb")


@tool(MitarbeiterSuchenParams, product="Digitale Personalakte", kind="read")
def mitarbeiter_suchen(p: MitarbeiterSuchenParams, ctx: Context) -> dict:
    """Sucht Mitarbeiter nach Name und/oder Abteilung und liefert ID, Rolle und Vorgesetzte.
    Immer zuerst verwenden, wenn ein Name statt einer Mitarbeiter-ID genannt wird."""
    hits = [m for m in MITARBEITER
            if (not p.suchbegriff or p.suchbegriff.lower() in m["name"].lower())
            and (not p.abteilung or p.abteilung.lower() == m["abteilung"].lower())]
    return {"anzahl": len(hits),
            "mitarbeiter": [{k: m[k] for k in ("id", "name", "abteilung", "rolle", "vorgesetzt")} for m in hits]}


# =====================================================================
# Urlaubsplaner
# =====================================================================
class UrlaubBeantragenParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    von: date = Field(description="Erster Urlaubstag (YYYY-MM-DD)")
    bis: date = Field(description="Letzter Urlaubstag (YYYY-MM-DD)")
    art: Literal["Erholungsurlaub", "Sonderurlaub", "Unbezahlter Urlaub", "Bildungsurlaub"] = "Erholungsurlaub"
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
    return {"antrag_id": f"UA-{ctx.call_id:05d}", "mitarbeiter_id": m["id"], "name": m["name"],
            "abteilung": m["abteilung"], "von": p.von.isoformat(), "bis": p.bis.isoformat(),
            "arbeitstage": tage, "art": p.art, "status": "beantragt", "genehmigung_durch": m["vorgesetzt"]}


def urlaubsanspruch_rest(mitarbeiter_id: str, jahr: int) -> int:
    used = sum(v["arbeitstage"] for v in _vacations()
               if v["mitarbeiter_id"] == mitarbeiter_id and v["von"].startswith(str(jahr))
               and v["art"] == "Erholungsurlaub" and v["status"] != "abgelehnt")
    return BY_ID[mitarbeiter_id]["urlaubstage"] - used


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
    return {"buchung_id": f"AZ-{ctx.call_id:05d}", "mitarbeiter_id": m["id"], "name": m["name"],
            "datum": p.datum.isoformat(), "beginn": p.beginn.strftime("%H:%M"), "ende": p.ende.strftime("%H:%M"),
            "pause_minuten": p.pause_minuten, "netto_stunden": round(minuten / 60, 2)}


class ZeitkontoParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    monat: str = Field(default_factory=lambda: date.today().strftime("%Y-%m"),
                       description="Monat im Format YYYY-MM", pattern=r"^\d{4}-\d{2}$")


@tool(ZeitkontoParams, product="Arbeitszeitverwaltung", kind="read")
def zeitkonto_abfragen(p: ZeitkontoParams, ctx: Context) -> dict:
    """Zeigt Soll-, Ist-Stunden und Saldo (Überstunden) eines Mitarbeiters für einen Monat."""
    m = _person(p.mitarbeiter_id)
    y, mo = map(int, p.monat.split("-"))
    first = date(y, mo, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    soll = round(_workdays(first, last) * m["wochenstunden"] / 5, 2)
    gebucht = sum(c["result"]["netto_stunden"] for c in store.successful("arbeitszeit_buchen")
                  if c["result"]["mitarbeiter_id"] == m["id"] and c["result"]["datum"].startswith(p.monat))
    ist = round(soll + _stable_offset(m["id"] + p.monat, -6, 14) + gebucht, 2)
    return {"mitarbeiter_id": m["id"], "name": m["name"], "monat": p.monat, "soll_stunden": soll,
            "ist_stunden": ist, "saldo_stunden": round(ist - soll, 2), "davon_manuell_gebucht": round(gebucht, 2)}


# =====================================================================
# Personaleinsatzplaner
# =====================================================================
class SchichtZuweisenParams(BaseModel):
    mitarbeiter_id: str = MitarbeiterId
    datum: date
    schicht: Literal["Frühschicht", "Spätschicht", "Nachtschicht", "Bereitschaft"]
    standort: str = Field("Hauptsitz", description="Einsatzort")


@tool(SchichtZuweisenParams, product="Personaleinsatzplaner", kind="write")
def schicht_zuweisen(p: SchichtZuweisenParams, ctx: Context) -> dict:
    """Plant einen Mitarbeiter für eine Schicht an einem Tag ein. Prüft Urlaubskonflikte."""
    m = _person(p.mitarbeiter_id)
    clash = next((v for v in _vacations() if v["mitarbeiter_id"] == m["id"] and v["status"] != "abgelehnt"
                  and date.fromisoformat(v["von"]) <= p.datum <= date.fromisoformat(v["bis"])), None)
    if clash:
        raise ToolError(f"{m['name']} hat am {p.datum} Urlaub ({clash['antrag_id']}, {clash['status']}).")
    zeiten = {"Frühschicht": "06:00–14:00", "Spätschicht": "14:00–22:00",
              "Nachtschicht": "22:00–06:00", "Bereitschaft": "ganztägig"}
    return {"einsatz_id": f"EP-{ctx.call_id:05d}", "mitarbeiter_id": m["id"], "name": m["name"],
            "datum": p.datum.isoformat(), "schicht": p.schicht, "zeiten": zeiten[p.schicht], "standort": p.standort}


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
    return {"besuch_id": f"BV-{ctx.call_id:05d}", "besucher": p.name, "firma": p.firma,
            "datum": p.datum.isoformat(), "uhrzeit": p.uhrzeit.strftime("%H:%M"),
            "gastgeber": host["name"], "zweck": p.zweck, "besucherausweis": f"B-{ctx.call_id % 1000:03d}",
            "benachrichtigt": host["id"]}


# =====================================================================
# Aufgabenmanagement
# =====================================================================
class AufgabeErstellenParams(BaseModel):
    titel: str = Field(min_length=3)
    beschreibung: str | None = None
    zustaendig_id: str = Field(description="Mitarbeiter-ID der zuständigen Person", pattern=r"^MA-\d{4}$")
    faellig_am: date | None = None
    prioritaet: Literal["niedrig", "normal", "hoch", "dringend"] = "normal"


@tool(AufgabeErstellenParams, product="Aufgabenmanagement", kind="write")
def aufgabe_erstellen(p: AufgabeErstellenParams, ctx: Context) -> dict:
    """Legt eine Aufgabe an und weist sie einem Mitarbeiter zu."""
    m = _person(p.zustaendig_id)
    return {"aufgabe_id": f"AG-{ctx.call_id:05d}", "titel": p.titel, "zustaendig": m["name"],
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
    return {"post_id": f"PB-{ctx.call_id:05d}", "absender": p.absender, "empfaenger": m["name"],
            "betreff": p.betreff, "art": p.art, "eingang_am": p.eingang_am.isoformat(),
            "fristrelevant": p.art == "Einschreiben", "benachrichtigt": m["id"]}
