"""Router test: does each model send questions to "wissen" or "agent" correctly?

    python -m evals.router                                     # all models, all cases
    python -m evals.router qwen3.6-35b-a3b mistral-small-3.2   # only these
    python -m evals.router --q "Hat Leonie Überstunden?"       # one question
"""
import argparse
import statistics
import time

from ragbot.agent.router import route
from ragbot.llms import available, get_model
# =====================================================================
# Test suite: compare models on the same cases
# =====================================================================
def _hist(user: str, assistant: str) -> list[dict]:
    return [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]


CASES = [
    # --- wissen: general questions, how-tos, small talk ---
    ("Was kostet der Urlaubsplaner pro Mitarbeiter im Monat?", "wissen", None),
    ("Wie beantrage ich als Mitarbeiter Urlaub?", "wissen", None),
    ("Hallo! Wer bist du eigentlich?", "wissen", None),
    ("Welche Kündigungsfrist steht in den AGB?", "wissen", None),
    ("Ist die Arbeitszeiterfassung DSGVO-konform?", "wissen", None),
    ("Welche Terminals gibt es für die Zeiterfassung?", "wissen", None),
    ("Danke, das hat mir geholfen!", "wissen", None),
    # --- traps: system words, but a general question ---
    ("Kann man Besucher vorab anmelden? Wie funktioniert das?", "wissen", None),
    ("Was passiert mit meinem Resturlaub am Jahresende?", "wissen", None),
    # --- agent: concrete people, data, actions ---
    ("Wie viel Resturlaub hat Jonas Weber noch?", "agent", None),
    ("Trag für Anna Schneider vom 16. bis 18. Dezember Urlaub ein", "agent", None),
    ("Wer ist nächste Woche in der Entwicklung abwesend?", "agent", None),
    ("Leg eine Aufgabe für Mehmet an: Terminal im Lager prüfen, bis Freitag", "agent", None),
    ("Hat Leonie diesen Monat Überstunden?", "agent", None),
    ("Melde Frau Petra Klein von der Muster GmbH für Donnerstag 10 Uhr bei Anna an", "agent", None),
    ("Kann Lukas am Samstag die Frühschicht übernehmen?", "agent", None),
    ("Für Jonas bitte nächste Woche Montag Urlaub", "agent", None),
    # --- mixed: knowledge + data → agent (it can also search the knowledge base) ---
    ("Was kostet der Urlaubsplaner und wie viel Resturlaub hat Jonas?", "agent", None),
    # --- follow-ups: only understandable with the conversation ---
    ("Und Anna?", "agent", _hist("Wie viel Resturlaub hat Jonas Weber?", "Jonas Weber hat noch 28 Tage Resturlaub.")),
    ("Und die Besucherverwaltung?", "wissen", _hist("Was kostet der Urlaubsplaner?", "Der Urlaubsplaner kostet 1,50 € pro Mitarbeiter und Monat.")),
    ("Danke! Mach das Gleiche für Jonas.", "agent", _hist("Trag für Anna nächste Woche Urlaub ein", "Erledigt, Antrag UA-00012 ist gestellt.")),
]


def run_suite(name: str, cases: list) -> dict | None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    model = get_model(name)                      # unloads the previous model
    t0 = time.perf_counter()
    try:
        model.load()
    except Exception as e:
        print(f"  übersprungen: {e}")
        return None
    print(f"  geladen in {time.perf_counter() - t0:.1f}s")
    route("Hallo", model=model)                  # warm-up: first call is always slower

    times, wrong = [], []
    for question, expected, history in cases:
        t0 = time.perf_counter()
        r = route(question, history, model=model)
        ms = (time.perf_counter() - t0) * 1000
        times.append(ms)
        if expected is None:
            mark = " "
        elif r["route"] == expected:
            mark = "✓"
        else:
            mark = "✗"
            wrong.append(question)
        ctx = " (mit Verlauf)" if history else ""
        miss = f"   ← erwartet {expected}" if mark == "✗" else ""
        print(f"  {mark} {r['route']:6} {ms:6.0f} ms  {question}{ctx}{miss}\n             ↳ {r['grund']}")

    scored = [c for c in cases if c[1] is not None]
    return {"model": name, "correct": len(scored) - len(wrong), "total": len(scored),
            "avg": statistics.mean(times), "max": max(times), "wrong": wrong}


def print_summary(results: list[dict]):
    print(f"\n{'=' * 70}\nVERGLEICH\n{'=' * 70}")
    print(f"  {'Modell':22} {'Richtig':>9} {'Ø Zeit':>9} {'Max':>9}")
    for r in sorted(results, key=lambda r: (-r["correct"], r["avg"])):
        print(f"  {r['model']:22} {r['correct']:>4}/{r['total']:<4} {r['avg']:>7.0f}ms {r['max']:>7.0f}ms")
    for r in results:
        if r["wrong"]:
            print(f"\n  {r['model']} lag falsch bei:")
            for q in r["wrong"]:
                print(f"    - {q}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", help=f"Modelle (Standard: alle): {', '.join(available())}")
    ap.add_argument("--q", help="nur diese eine Frage testen")
    args = ap.parse_args()

    cases = [(args.q, None, None)] if args.q else CASES
    results = [r for name in (args.models or available()) if (r := run_suite(name, cases))]
    if results and not args.q:
        print_summary(results)