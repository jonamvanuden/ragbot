"""Router: decides per message who answers – and for the agent, how it works.

One short LLM call; the answer is forced into JSON by a schema:
    {"route": "wissen" | "agent", "modus": "toolbox" | "code", "grund": "..."}

  wissen           → knowledge base (RAG)
  agent + toolbox  → the agent with single tools (lookups for one person, everything that changes data)
  agent + code     → code mode: the LLM writes a script against the SDK (rankings, sums, bulk changes as a plan)

If anything goes wrong, the toolbox agent takes over (it can also search the knowledge base).

Test: python -m evals.router
"""
from ragbot.llms import get_model

ROUTER_SYSTEM = """Du bist der Router des Personal-Planer-Assistenten. Entscheide für die letzte Nachricht des Benutzers, wer antworten soll.

"agent": Die Nachricht braucht Daten aus dem Personal-Planer-System oder soll dort etwas tun.
Das betrifft konkrete Mitarbeiter, Urlaub und Resturlaub, Abwesenheiten, Arbeitszeiten und Zeitkonten,
Schichten, Besucher, Aufgaben oder Post. Auch reine Abfragen wie "Wie viel Resturlaub hat Jonas?" gehören hierher,
ebenso Listen und Übersichten über Mitarbeiter, Abteilungen oder Abwesenheiten.

"wissen": Allgemeine Fragen zu Produkten, Funktionen, Bedienung, Preisen, Verträgen, AGB, Datenschutz
oder zur Firma Personal-Planer, Fragen zu den Hilfeartikeln, sowie Begrüßungen und Smalltalk.

Beispiele:
"Was kostet der Urlaubsplaner?" → wissen
"Wie beantrage ich Urlaub im Urlaubsplaner?" → wissen
"Trag für Anna nächste Woche Urlaub ein" → agent
"Wer ist morgen im Support abwesend?" → agent
"Melde Herrn Klein für Donnerstag 10 Uhr bei Anna an" → agent
"Zeig mir alle Mitarbeiter" → agent
"Und was ist mit Frau Schneider?" → agent
"Hallo, was kannst du?" → wissen
Nennt die Nachricht einen Mitarbeiter oder eine Person (Vorname, Nachname, „Herr …“, „Frau …“), wähle "agent".
Bezieht sich die Nachricht auf das vorherige Gespräch (z. B. "und für Jonas auch"), entscheide wie dort.

Bei "agent" wählst du zusätzlich den "modus":
"code": Auswertungen über viele Datensätze: Rankings, Summen, Durchschnitte, Statistiken, Vergleiche,
"wer am meisten / am häufigsten …", "wie viele … insgesamt", Überstunden ganzer Abteilungen, Zeiträume über viele Personen.
Außerdem Massenänderungen nach einer Regel, die mehrere Datensätze betreffen ("alle offenen Anträge im Support genehmigen",
"für jeden im Vertrieb eine Aufgabe anlegen") – sie werden geplant und dem Benutzer gesammelt zur Freigabe vorgelegt.
"toolbox": eine konkrete Abfrage zu einer Person oder einem Antrag, einfache Listen,
und jede EINZELNE Änderung (einen Urlaub eintragen, einen Antrag genehmigen, eine Buchung, einen Besucher anmelden).
Beispiele:
"Wer war dieses Jahr am häufigsten krank?" → agent, code
"Wie viele Überstunden hat jede Abteilung im Oktober?" → agent, code
"Welche Abteilung hat die meisten offenen Urlaubsanträge?" → agent, code
"Wie viel Resturlaub hat Jonas?" → agent, toolbox
"Genehmige den Antrag UA-00031" → agent, toolbox
"Genehmige alle offenen Urlaubsanträge im Support" → agent, code
Bei "wissen" setze "modus" auf "toolbox".

Antworte nur mit JSON: "route" ("wissen" oder "agent"), "modus" ("toolbox" oder "code") und "grund" (ein kurzer Satz)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["wissen", "agent"]},
        "modus": {"type": "string", "enum": ["toolbox", "code"]},
        "grund": {"type": "string"},
    },
    "required": ["route", "modus", "grund"],
}


def route(question: str, history: list[dict] | None = None, model=None) -> dict:
    """Returns {"route": "wissen" | "agent", "modus": "toolbox" | "code", "grund": "..."}. Never raises.
    `model`: optional, a specific model object (used by evals/router.py); default = the active model."""
    model = model or get_model()
    hist = "\n".join(f"{'Benutzer' if m['role'] == 'user' else 'Assistent'}: {m['content'][:300]}"
                     for m in (history or [])[-4:])
    user = (f"Bisheriges Gespräch:\n{hist}\n\n" if hist else "") + f"Letzte Nachricht: {question}"
    try:
        d = model.ask_json([{"role": "system", "content": ROUTER_SYSTEM},
                            {"role": "user", "content": user}], SCHEMA, max_tokens=150)
    except Exception as e:
        return {"route": "agent", "modus": "toolbox", "grund": f"Router-Fehler, der Agent übernimmt ({e})"}
    if d.get("route") not in ("wissen", "agent"):
        return {"route": "agent", "modus": "toolbox", "grund": "Unklare Router-Antwort, der Agent übernimmt"}
    modus = d.get("modus") if d.get("route") == "agent" and d.get("modus") in ("toolbox", "code") else "toolbox"
    return {"route": d["route"], "modus": modus, "grund": str(d.get("grund", ""))[:200]}