"""The ERP SDK for code mode: the only thing generated code can call.

    from ragbot.codemode.sdk import erp
    erp.mitarbeiter.liste(abteilung="Support")

Reading goes straight to the database, opened READ-ONLY (mode=ro).

Writing never happens here: the write methods (beantragen, entscheiden, buchen, zuweisen,
anmelden, erstellen) only add an entry to PLAN. After the script has run, the user sees the
whole plan and approves it; only then the graph executes each entry with the normal toolbox
tool (same rules, same audit log).

Every method returns plain lists/dicts with dates as "YYYY-MM-DD" strings,
so generated code can compare and sort them directly.
"""
import inspect
import sqlite3

from ragbot import config
from ragbot.erp import rules


def _q(sql: str, *args) -> list[dict]:
    conn = sqlite3.connect(f"file:{config.ERP_DB}?mode=ro", uri=True)   # read-only connection
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


PLAN: list[dict] = []          # planned changes of the current script run (one fresh process per run)
MAX_PLAN = 25


def _plan(tool: str, text: str, **args) -> dict:
    """Record a change instead of doing it."""
    if len(PLAN) >= MAX_PLAN:
        raise ValueError(f"Mehr als {MAX_PLAN} Änderungen in einem Lauf sind nicht erlaubt.")
    PLAN.append({"tool": tool, "text": text, "args": {k: v for k, v in args.items() if v is not None}})
    return {"geplant": True, "nr": len(PLAN)}


def _name(mitarbeiter_id: str) -> str:
    rows = _q("SELECT vorname || ' ' || nachname AS name FROM mitarbeiter WHERE id = ?", mitarbeiter_id)
    return rows[0]["name"] if rows else mitarbeiter_id


def _where(filters: list[tuple[str, object]]) -> tuple[str, list]:
    """Build 'WHERE a = ? AND b = ?' from the filters that are not None."""
    parts = [(cond, val) for cond, val in filters if val is not None]
    if not parts:
        return "", []
    return " WHERE " + " AND ".join(c for c, _ in parts), [v for _, v in parts]


PERSON = """SELECT m.id, m.vorname || ' ' || m.nachname AS name, m.vorname, m.nachname, a.name AS abteilung,
                   m.rolle, m.vorgesetzt_id, m.eintritt, m.wochenstunden, m.urlaubstage
            FROM mitarbeiter m JOIN abteilungen a ON a.id = m.abteilung_id"""


class Mitarbeiter:
    def liste(self, abteilung: str | None = None, name: str | None = None) -> list[dict]:
        """Alle aktiven Mitarbeiter, optional gefiltert nach Abteilung oder Namensteil.
        Felder: id, name, vorname, nachname, abteilung, rolle, vorgesetzt_id, eintritt, wochenstunden, urlaubstage"""
        w, a = _where([("m.aktiv = ?", 1), ("a.name LIKE ?", abteilung),
                       ("(m.vorname || ' ' || m.nachname) LIKE ?", f"%{name}%" if name else None)])
        return _q(PERSON + w + " ORDER BY m.nachname", *a)

    def get(self, mitarbeiter_id: str) -> dict | None:
        """Ein Mitarbeiter per ID (z. B. "MA-1002"), oder None. Felder wie bei liste()."""
        rows = _q(PERSON + " WHERE m.id = ?", mitarbeiter_id)
        return rows[0] if rows else None


class Abteilungen:
    def liste(self) -> list[dict]:
        """Alle Abteilungen. Felder: id, name, leitung_id, leitung_name, anzahl_mitarbeiter"""
        return _q("""SELECT a.id, a.name, a.leitung_id, l.vorname || ' ' || l.nachname AS leitung_name,
                            (SELECT COUNT(*) FROM mitarbeiter m WHERE m.abteilung_id = a.id AND m.aktiv = 1)
                              AS anzahl_mitarbeiter
                     FROM abteilungen a LEFT JOIN mitarbeiter l ON l.id = a.leitung_id ORDER BY a.name""")


class Urlaub:
    def antraege(self, mitarbeiter_id: str | None = None, von: str | None = None, bis: str | None = None,
                 status: str | None = None, art: str | None = None) -> list[dict]:
        """Urlaubsanträge, die den Zeitraum von–bis berühren (beide optional).
        status: beantragt | genehmigt | abgelehnt | storniert. art: Erholungsurlaub | Sonderurlaub | Unbezahlter Urlaub
        Felder: id, mitarbeiter_id, name, abteilung, von, bis, arbeitstage, art, status, beantragt_am, entschieden_von"""
        w, a = _where([("u.mitarbeiter_id = ?", mitarbeiter_id), ("u.bis >= ?", von), ("u.von <= ?", bis),
                       ("u.status = ?", status), ("u.art = ?", art)])
        return _q("""SELECT u.id, u.mitarbeiter_id, m.vorname || ' ' || m.nachname AS name, ab.name AS abteilung,
                            u.von, u.bis, u.arbeitstage, u.art, u.status, u.beantragt_am, u.entschieden_von
                     FROM urlaubsantraege u JOIN mitarbeiter m ON m.id = u.mitarbeiter_id
                     JOIN abteilungen ab ON ab.id = m.abteilung_id""" + w + " ORDER BY u.von", *a)


    def beantragen(self, mitarbeiter_id: str, von: str, bis: str, art: str = "Erholungsurlaub",
                   kommentar: str | None = None) -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Urlaubsantrag stellen.
        art: Erholungsurlaub | Sonderurlaub | Unbezahlter Urlaub"""
        return _plan("urlaub_beantragen", f"{_name(mitarbeiter_id)}: {art} {von} bis {bis}",
                     mitarbeiter_id=mitarbeiter_id, von=von, bis=bis, art=art, kommentar=kommentar)

    def entscheiden(self, antrag_id: str, entscheidung: str, begruendung: str | None = None) -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Antrag genehmigen oder ablehnen.
        entscheidung: genehmigt | abgelehnt (Ablehnung braucht begruendung)"""
        a = _q("""SELECT m.vorname || ' ' || m.nachname AS name, u.von, u.bis FROM urlaubsantraege u
                  JOIN mitarbeiter m ON m.id = u.mitarbeiter_id WHERE u.id = ?""", antrag_id)
        who = f"{a[0]['name']}, {a[0]['von']} bis {a[0]['bis']}" if a else "unbekannter Antrag"
        return _plan("urlaubsantrag_entscheiden", f"{antrag_id} ({who}) → {entscheidung}",
                     antrag_id=antrag_id, entscheidung=entscheidung, begruendung=begruendung)


class Krank:
    def meldungen(self, mitarbeiter_id: str | None = None, von: str | None = None,
                  bis: str | None = None) -> list[dict]:
        """Krankmeldungen, die den Zeitraum von–bis berühren (beide optional).
        Felder: id, mitarbeiter_id, name, abteilung, von, bis, arbeitstage, au_bescheinigung (True/False)"""
        w, a = _where([("k.mitarbeiter_id = ?", mitarbeiter_id), ("k.bis >= ?", von), ("k.von <= ?", bis)])
        rows = _q("""SELECT k.id, k.mitarbeiter_id, m.vorname || ' ' || m.nachname AS name, ab.name AS abteilung,
                            k.von, k.bis, k.arbeitstage, k.au_bescheinigung
                     FROM krankmeldungen k JOIN mitarbeiter m ON m.id = k.mitarbeiter_id
                     JOIN abteilungen ab ON ab.id = m.abteilung_id""" + w + " ORDER BY k.von", *a)
        for r in rows:
            r["au_bescheinigung"] = bool(r["au_bescheinigung"])
        return rows


class Zeit:
    def buchungen(self, mitarbeiter_id: str | None = None, von: str | None = None,
                  bis: str | None = None) -> list[dict]:
        """Zeitbuchungen (ein Eintrag pro Arbeitstag), Datum zwischen von und bis (beide optional).
        Felder: mitarbeiter_id, datum, beginn ("HH:MM"), ende, pause_minuten, netto_stunden, quelle (terminal | manuell)"""
        w, a = _where([("mitarbeiter_id = ?", mitarbeiter_id), ("datum >= ?", von), ("datum <= ?", bis)])
        return _q("SELECT mitarbeiter_id, datum, beginn, ende, pause_minuten, netto_stunden, quelle "
                  "FROM zeitbuchungen" + w + " ORDER BY datum", *a)


    def konto(self, mitarbeiter_id: str, monat: str) -> dict:
        """Zeitkonto eines Monats ("YYYY-MM") nach den offiziellen Regeln: Soll aus Arbeitstagen ohne Feiertage,
        genehmigter Urlaub und Krankheit zählen nicht zum Soll, nur vergangene Tage. Für Überstunden IMMER dies nutzen.
        Felder: monat, zeitraum, soll_stunden, ist_stunden, saldo_stunden (= Überstunden, negativ = Minusstunden),
        gebuchte_tage, abwesenheitstage"""
        m = _q("SELECT wochenstunden FROM mitarbeiter WHERE id = ?", mitarbeiter_id)
        if not m:
            raise ValueError(f"Mitarbeiter {mitarbeiter_id} existiert nicht.")
        return rules.zeitkonto(_q, mitarbeiter_id, m[0]["wochenstunden"], monat)


    def buchen(self, mitarbeiter_id: str, datum: str, beginn: str, ende: str, pause_minuten: int = 30) -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Arbeitszeit für einen Tag buchen.
        beginn/ende im Format HH:MM"""
        return _plan("arbeitszeit_buchen", f"{_name(mitarbeiter_id)}: {datum} {beginn}–{ende}, {pause_minuten} min Pause",
                     mitarbeiter_id=mitarbeiter_id, datum=datum, beginn=beginn, ende=ende, pause_minuten=pause_minuten)


class Schichten:
    def liste(self, von: str | None = None, bis: str | None = None,
              mitarbeiter_id: str | None = None) -> list[dict]:
        """Geplante Schichten. typ: Früh | Spät | Nacht.
        Felder: datum, typ, mitarbeiter_id, name, standort"""
        w, a = _where([("s.datum >= ?", von), ("s.datum <= ?", bis), ("s.mitarbeiter_id = ?", mitarbeiter_id)])
        return _q("""SELECT s.datum, s.typ, s.mitarbeiter_id, m.vorname || ' ' || m.nachname AS name, s.standort
                     FROM schichten s JOIN mitarbeiter m ON m.id = s.mitarbeiter_id""" + w +
                  " ORDER BY s.datum, s.typ", *a)


    def zuweisen(self, mitarbeiter_id: str, datum: str, schicht: str, standort: str = "Euskirchen") -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Mitarbeiter für eine Schicht einplanen.
        schicht: Früh | Spät | Nacht"""
        schicht = schicht if schicht.endswith("schicht") else f"{schicht}schicht"
        return _plan("schicht_zuweisen", f"{_name(mitarbeiter_id)}: {schicht} am {datum}",
                     mitarbeiter_id=mitarbeiter_id, datum=datum, schicht=schicht, standort=standort)


class Besucher:
    def liste(self, von: str | None = None, bis: str | None = None) -> list[dict]:
        """Angemeldete Besucher. status: angemeldet | eingetroffen | abgesagt.
        Felder: id, name, firma, gastgeber_id, gastgeber, datum, uhrzeit, status"""
        w, a = _where([("b.datum >= ?", von), ("b.datum <= ?", bis)])
        return _q("""SELECT b.id, b.name, b.firma, b.gastgeber_id, m.vorname || ' ' || m.nachname AS gastgeber,
                            b.datum, b.uhrzeit, b.status
                     FROM besucher b JOIN mitarbeiter m ON m.id = b.gastgeber_id""" + w +
                  " ORDER BY b.datum, b.uhrzeit", *a)


    def anmelden(self, name: str, datum: str, uhrzeit: str, gastgeber_id: str, firma: str | None = None,
                 zweck: str | None = None) -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Besucher anmelden. uhrzeit im Format HH:MM"""
        return _plan("besucher_anmelden", f"{name} ({firma or 'ohne Firma'}) am {datum} {uhrzeit} bei {_name(gastgeber_id)}",
                     name=name, datum=datum, uhrzeit=uhrzeit, gastgeber_id=gastgeber_id, firma=firma, zweck=zweck)


class Aufgaben:
    def liste(self, mitarbeiter_id: str | None = None, status: str | None = None) -> list[dict]:
        """Aufgaben. status: offen | in Arbeit | erledigt. prioritaet: niedrig | normal | hoch.
        Felder: id, titel, zugewiesen_id, zugewiesen, erstellt_von, faellig, prioritaet, status"""
        w, a = _where([("t.zugewiesen_id = ?", mitarbeiter_id), ("t.status = ?", status)])
        return _q("""SELECT t.id, t.titel, t.zugewiesen_id, m.vorname || ' ' || m.nachname AS zugewiesen,
                            t.erstellt_von, t.faellig, t.prioritaet, t.status
                     FROM aufgaben t JOIN mitarbeiter m ON m.id = t.zugewiesen_id""" + w +
                  " ORDER BY t.faellig", *a)


    def erstellen(self, titel: str, zustaendig_id: str, faellig_am: str | None = None, prioritaet: str = "normal",
                  beschreibung: str | None = None) -> dict:
        """ÄNDERUNG (wird nur geplant, ausgeführt erst nach Freigabe): Aufgabe anlegen.
        prioritaet: niedrig | normal | hoch"""
        return _plan("aufgabe_erstellen", f"„{titel}“ für {_name(zustaendig_id)}"
                     + (f", fällig {faellig_am}" if faellig_am else "") + f", Priorität {prioritaet}",
                     titel=titel, zustaendig_id=zustaendig_id, faellig_am=faellig_am, prioritaet=prioritaet,
                     beschreibung=beschreibung)


class ERP:
    mitarbeiter = Mitarbeiter()
    abteilungen = Abteilungen()
    urlaub = Urlaub()
    krank = Krank()
    zeit = Zeit()
    schichten = Schichten()
    besucher = Besucher()
    aufgaben = Aufgaben()


erp = ERP()
AREAS = ["mitarbeiter", "abteilungen", "urlaub", "krank", "zeit", "schichten", "besucher", "aufgaben"]


def docs() -> str:
    """SDK description for the LLM prompt, generated from the signatures and docstrings above."""
    lines = []
    for area in AREAS:
        for name, fn in inspect.getmembers(getattr(erp, area), inspect.ismethod):
            if name.startswith("_"):
                continue
            sig = str(inspect.signature(fn)).replace("'", "")
            doc = "\n    ".join(line.strip() for line in inspect.getdoc(fn).splitlines())
            lines.append(f"erp.{area}.{name}{sig}\n    {doc}")
    return "\n".join(lines)