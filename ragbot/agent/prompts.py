"""Prompts for the two LLM jobs in the agent:
  1. decide  – pick the next step as JSON (which tool, which arguments, or 'fertig')
  2. respond – write the final German answer from the results of those steps
"""
import json
from datetime import date

from ragbot import config
from ragbot.tools import TOOLS

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

DECIDE_SYSTEM = """Du bist der Agent von Personal-Planer, dem HR-Systemhaus. Du erledigst Aufgaben in den
Personal-Planer-Modulen und beantwortest Fragen. Heute ist {wochentag}, der {heute}.

Werkzeuge:
{tools}

Regeln:
1. Jeder Schritt ist genau ein Werkzeug-Aufruf oder "fertig".
2. Wird ein Mitarbeiter nur mit Namen genannt, rufe zuerst mitarbeiter_suchen auf. Erfinde niemals IDs.
3. Fragen zu Produkten, Preisen, Verträgen oder Funktionen: wissen_suchen.
4. Datumsangaben immer als YYYY-MM-DD. Rechne relative Angaben wie "nächste Woche" vom heutigen Datum aus.
5. Wähle "fertig", sobald du antworten kannst, oder wenn eine Angabe fehlt, die nur der Benutzer kennt.
6. Rufe kein Werkzeug zweimal mit denselben Parametern auf. Ist ein Schritt fehlgeschlagen, korrigiere die Parameter oder wähle "fertig".
7. Werkzeuge, die Daten ändern, werden dem Benutzer vor der Ausführung zur Freigabe vorgelegt.

Antworte ausschließlich mit einem JSON-Objekt mit den Feldern
"gedanke" (ein kurzer Satz auf Deutsch), "aktion" ("tool" oder "fertig"),
und bei "tool" zusätzlich "tool" (Name des Werkzeugs) und "args" (Objekt mit den Parametern)."""

RESPOND_SYSTEM = config.SYSTEM_PROMPT + (
    " Du bekommst die Ergebnisse der Schritte, die du im Personal-Planer-System ausgeführt hast."
    " Nenne konkrete Ergebnisse wie Antragsnummern, Daten und Zahlen."
    " War ein Schritt fehlerhaft oder vom Benutzer abgelehnt, erkläre das ehrlich."
    " Behaupte nie, etwas sei erledigt, wenn es nicht ausgeführt wurde."
    " Fehlt eine Angabe, frage gezielt danach."
    " Informationen aus wissen_suchen zitierst du mit dem Titel der Quelle in eckigen Klammern."
)


def _type(prop: dict) -> str:
    if "enum" in prop:
        return " | ".join(map(str, prop["enum"]))
    if "anyOf" in prop:
        return " | ".join(_type(p) for p in prop["anyOf"] if p.get("type") != "null")
    return {"date": "YYYY-MM-DD", "time": "HH:MM"}.get(prop.get("format"), prop.get("type", "any"))


def tool_catalog() -> str:
    """All tools with their parameters, compact enough for a small model's prompt."""
    blocks = []
    for t in TOOLS.values():
        schema = t.params.model_json_schema()
        required = set(schema.get("required", []))
        params = []
        for name, prop in schema.get("properties", {}).items():
            optional = "" if name in required else " (optional)"
            desc = f" – {prop['description']}" if prop.get("description") else ""
            params.append(f"{name}: {_type(prop)}{optional}{desc}")
        kind = "lesen" if t.kind == "read" else "ÄNDERT DATEN, braucht Freigabe"
        blocks.append(f"- {t.name} [{kind}]: {' '.join(t.description.split())}\n    "
                      + "\n    ".join(params or ["(keine Parameter)"]))
    return "\n".join(blocks)


def decision_schema() -> dict:
    """JSON schema the model's answer is forced into (llama.cpp turns it into a grammar)."""
    return {
        "type": "object",
        "properties": {
            "gedanke": {"type": "string"},
            "aktion": {"type": "string", "enum": ["tool", "fertig"]},
            "tool": {"type": "string", "enum": list(TOOLS)},
            "args": {"type": "object"},
        },
        "required": ["gedanke", "aktion"],
    }


def format_steps(steps: list[dict], limit: int = 1500) -> str:
    lines = []
    for i, s in enumerate(steps, 1):
        args = json.dumps(s["args"], ensure_ascii=False)
        res = (json.dumps(s.get("result"), ensure_ascii=False, default=str) if s["status"] == "ok"
               else f"FEHLER: {s.get('error')}")
        if len(res) > limit:
            res = res[:limit] + " …(gekürzt)"
        lines.append(f"{i}. {s['tool']}({args}) → {s['status']}: {res}")
    return "\n".join(lines)


def _history_text(history: list[dict]) -> str:
    return "\n".join(f"{'Benutzer' if m['role'] == 'user' else 'Assistent'}: {m['content'][:500]}"
                     for m in history[-6:])


def decide_messages(state: dict) -> list[dict]:
    today = date.today()
    system = DECIDE_SYSTEM.format(heute=today.isoformat(), wochentag=WEEKDAYS[today.weekday()], tools=tool_catalog())
    steps = state.get("steps", [])
    hist = _history_text(state.get("history", []))
    user = (f"Bisheriges Gespräch:\n{hist}\n\n" if hist else "") + f"Aktuelle Anfrage: {state['question']}\n\n"
    user += ("Bisherige Schritte:\n" + format_steps(steps)) if steps else "Bisherige Schritte: keine"
    user += "\n\nWas ist der nächste Schritt? Antworte als JSON."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def respond_messages(state: dict) -> list[dict]:
    steps = state.get("steps", [])
    user = f"Anfrage: {state['question']}\n\n"
    user += ("Ergebnisse der ausgeführten Schritte:\n" + format_steps(steps, limit=3000)) if steps \
        else "Es wurden keine Werkzeuge benutzt."
    user += "\n\nFormuliere jetzt die Antwort an den Benutzer."
    history = [{"role": m["role"], "content": m["content"]} for m in state.get("history", [])[-6:]]
    return [{"role": "system", "content": RESPOND_SYSTEM}] + history + [{"role": "user", "content": user}]
