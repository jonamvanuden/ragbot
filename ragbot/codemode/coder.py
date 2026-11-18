"""Code mode: the LLM writes a short Python script against the SDK, we run it, and fix it if needed.

    from ragbot.codemode.coder import solve
    r = solve("Wer war 2026 am häufigsten krank?")
    r["output"]   -> what the script printed (the facts for the answer)
    r["attempts"] -> every try: code, output, error

Try it in the terminal (server stopped – only one model fits on the GPU):
    python -m ragbot.codemode.coder "Wer war 2026 am häufigsten krank?"
"""
import json
import sys
from datetime import date, timedelta

from ragbot.codemode.executor import run
from ragbot.codemode.sdk import docs
from ragbot.llms import get_model

MAX_ATTEMPTS = 3
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

SYSTEM = """Du schreibst kurzen Python-Code, der Fragen zum Personal-Planer-ERP beantwortet.
Heute ist {wochentag}, der {heute}.

Kalender (verwende diese Daten, rechne Zeiträume nicht selbst aus):
{kalender}

Ohne import verfügbar: erp (das SDK unten), heute (String "YYYY-MM-DD"), date, timedelta, Counter, defaultdict, print.

SDK:
{sdk}

Regeln:
0. Funktionen mit "ÄNDERUNG" führen nichts sofort aus, sie PLANEN nur. Der Benutzer sieht danach alle geplanten
   Änderungen und gibt sie frei. Plane nur, was der Benutzer ausdrücklich verlangt, und gib mit print() aus,
   was du geplant hast (z. B. "3 Anträge zur Genehmigung vorgemerkt: …"). Bei reinen Fragen nichts planen.
1. Kein import. Rufe nur die SDK-Funktionen oben auf, keine anderen Attribute von erp.
2. Überstunden, Soll- und Ist-Stunden immer mit erp.zeit.konto() berechnen, nie selbst aus Buchungen schätzen.
   Für mehrere Mitarbeiter: für jeden erp.zeit.konto() aufrufen und die Werte summieren.
3. Gib das Ergebnis mit print() aus: kompakt, beschriftet, mit Namen statt IDs, Zahlen sinnvoll gerundet.
4. Datumswerte sind Strings "YYYY-MM-DD" und lassen sich direkt vergleichen ("2026-03-01" < "2026-11-01").
   Für relative Angaben ("nächste Woche", "letzter Monat", "dieses Jahr") nimm die Daten aus dem Kalender oben.
5. Nutze die Filter der SDK-Funktionen, statt alles zu laden.
6. Höchstens etwa 30 Zeilen. Keine Funktionen oder Klassen definieren, außer wirklich nötig.

Beispiel – Frage: "Wie viele Mitarbeiter hat jede Abteilung?"
{{"plan": "Abteilungen laden und Anzahl ausgeben.", "code": "for a in erp.abteilungen.liste():\\n    print(f\\"{{a['name']}}: {{a['anzahl_mitarbeiter']}} Mitarbeiter (Leitung: {{a['leitung_name']}})\\")"}}

Antworte nur mit JSON: "plan" (ein kurzer Satz auf Deutsch) und "code" (der Python-Code)."""

SCHEMA = {
    "type": "object",
    "properties": {"plan": {"type": "string"}, "code": {"type": "string"}},
    "required": ["plan", "code"],
}


def calendar(today: date) -> str:
    """Ready-made date ranges, so the model never has to do date arithmetic itself."""
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    rows = [
        ("gestern", today - timedelta(days=1), None), ("heute", today, None), ("morgen", today + timedelta(days=1), None),
        ("diese Woche", monday, monday + timedelta(days=6)),
        ("letzte Woche", monday - timedelta(days=7), monday - timedelta(days=1)),
        ("nächste Woche", monday + timedelta(days=7), monday + timedelta(days=13)),
        ("übernächste Woche", monday + timedelta(days=14), monday + timedelta(days=20)),
        ("dieser Monat", first, next_first - timedelta(days=1)),
        ("letzter Monat", prev_last.replace(day=1), prev_last),
        ("nächster Monat", next_first, (next_first + timedelta(days=32)).replace(day=1) - timedelta(days=1)),
        ("dieses Jahr", date(today.year, 1, 1), date(today.year, 12, 31)),
        ("letztes Jahr", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
    ]
    out = []
    for name, a, b in rows:
        if b is None:
            out.append(f"- {name}: {WEEKDAYS[a.weekday()]}, {a}")
        else:
            out.append(f"- {name}: {a} bis {b}")
    return "\n".join(out)


def messages(question: str, history: list[dict] | None = None) -> list[dict]:
    today = date.today()
    system = SYSTEM.format(wochentag=WEEKDAYS[today.weekday()], heute=today.isoformat(),
                           kalender=calendar(today), sdk=docs())
    hist = "\n".join(f"{'Benutzer' if m['role'] == 'user' else 'Assistent'}: {m['content'][:300]}"
                     for m in (history or [])[-4:])
    user = (f"Bisheriges Gespräch:\n{hist}\n\n" if hist else "") + f"Frage: {question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def solve(question: str, history: list[dict] | None = None, model=None, on_attempt=None, validate=None) -> dict:
    """Write → run → fix, at most MAX_ATTEMPTS times. Never raises.
    on_attempt(attempt_dict) is called after every try (used for live events in the UI).
    validate(plan) -> error text or None: checks planned changes; an error counts like a failed run."""
    model = model or get_model()
    msgs = messages(question, history)
    attempts = []
    for n in range(1, MAX_ATTEMPTS + 1):
        try:
            d = model.ask_json(msgs, SCHEMA, max_tokens=1200)
            plan, code = str(d.get("plan", "")), str(d.get("code", ""))
        except Exception as e:
            plan, code = "", ""
            result = {"ok": False, "output": "", "error": f"Kein gültiges JSON vom Modell: {e}", "plan": [], "ms": 0.0}
        else:
            result = run(code)
            if result["ok"] and not result["output"].strip() and not result["plan"]:
                result = {**result, "ok": False, "error": "Der Code hat nichts ausgegeben. Gib das Ergebnis mit print() aus."}
            elif result["ok"] and result["plan"] and validate:
                problem = validate(result["plan"])
                if problem:
                    result = {**result, "ok": False, "error": f"Geplante Änderungen sind ungültig:\n{problem}"}

        attempt = {"n": n, "plan": plan, "code": code, **result}
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if result["ok"]:
            break
        # feed the error back: the model sees its own code and what went wrong
        msgs = msgs + [
            {"role": "assistant", "content": json.dumps({"plan": plan, "code": code}, ensure_ascii=False)},
            {"role": "user", "content": f"Der Code ist fehlgeschlagen:\n{result['error']}\n"
                                        "Korrigiere ihn und antworte wieder nur mit JSON (plan, code)."},
        ]
    last = attempts[-1]
    return {"ok": last["ok"], "code": last["code"], "output": last["output"], "error": last["error"],
            "plan": last["plan"] if last["ok"] else [], "attempts": attempts}


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Wer war 2026 am häufigsten krank?"
    m = get_model()
    print(f"Lade {m.name} …")
    m.load()

    def show(a):
        print(f"\n── Versuch {a['n']} ── {a['plan']}\n{a['code']}\n")
        print(f"→ {'OK' if a['ok'] else 'FEHLER: ' + str(a['error'])}  ({a['ms']} ms)")
        if a["output"]:
            print(a["output"])
        for i, p in enumerate(a.get("plan") or [], 1):
            print(f"   geplant {i}: {p['tool']} – {p['text']}")

    r = solve(question, model=m, on_attempt=show)
    print(f"\nErgebnis: {'✓' if r['ok'] else '✗'} nach {len(r['attempts'])} Versuch(en)")