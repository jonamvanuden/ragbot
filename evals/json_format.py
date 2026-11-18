"""JSON test: how exactly does each model produce JSON in a given format – from tiny to huge?

Levels (formats defined as Pydantic classes, generated via generate() below):
  L1 flach        2 fields
  L2 typen        enum, int, bool
  L3 verschachtelt  nested object, date, time, 5-digit postcode
  L4 liste        list of objects + a computed total
  L5 tief         department → teams → people → absences (4 levels)
  L6 gross        employee file with ~18 fields, optionals, nested parts
  L7 riesig       weekly shift plan: 7 days × shifts × people, with rules
plus the real agent decisions (right tool, valid + correct arguments).

Checks per task:  JSON (parses) · Valide (passes the spec) · Inhalt (right values) · Versuche (1 = first try)

Crash-safe: every model runs in its own process. If llama.cpp crashes (segfault), the test
notes it, restarts that model and continues with the next task.

Run (server must be stopped – only one model fits on the GPU):
    python -m ragbot.agent.json_test                      # all models from registry.py
    python -m ragbot.agent.json_test qwen3.6-35b-a3b      # only this model
"""
import argparse
import json
import multiprocessing as mp
import statistics
import time
from datetime import date, time as dtime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

TODAY = date.today()
NEXT_MON = TODAY + timedelta(days=7 - TODAY.weekday())


# =====================================================================
# Structured output: JSON in a format you define as a Pydantic class
#   obj, attempts = generate(model, messages, MyFormat, max_tokens=500)
# 1. schema cleaned for llama.cpp's grammar (refs inlined, regex 'pattern' removed – it crashes the converter)
# 2. the grammar forces the shape, Pydantic checks the details (dates, patterns, enums …)
# 3. on a validation error the model gets the error message and one more try
# =====================================================================
# keys the llama.cpp grammar converter can't handle (or doesn't need) – checked by Pydantic instead
DROP = {"pattern", "format", "title", "default", "examples", "minLength", "maxLength", "$defs"}


class StructuredError(Exception):
    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def grammar_schema(schema: dict) -> dict:
    """Make a JSON schema safe for llama.cpp: inline $refs, drop unsupported keywords."""
    defs = schema.get("$defs", {})

    def walk(node, in_properties=False):
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        if in_properties:                       # keys here are field names – keep them all
            return {k: walk(v) for k, v in node.items()}
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        return {k: walk(v, in_properties=(k == "properties")) for k, v in node.items() if k not in DROP}

    return walk(schema)


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'objekt'}: {x['msg']}" for x in e.errors())


def generate(model, messages: list[dict], spec: type[BaseModel], max_tokens: int = 1024,
             retries: int = 1) -> tuple[BaseModel, list[dict]]:
    """Returns (validated object, attempts). Raises StructuredError if all attempts fail."""
    schema = grammar_schema(spec.model_json_schema())
    attempts, msgs, error = [], list(messages), ""
    for _ in range(retries + 1):
        t0 = time.perf_counter()
        raw = None
        try:
            raw = model.ask_json(msgs, schema, max_tokens=max_tokens)
            obj = spec.model_validate(raw)
            attempts.append({"ok": True, "ms": (time.perf_counter() - t0) * 1000, "raw": raw})
            return obj, attempts
        except ValidationError as e:
            error = _errors(e)
        except Exception as e:                      # not even valid JSON (e.g. cut off at max_tokens)
            error = f"kein gültiges JSON: {e}"
        attempts.append({"ok": False, "ms": (time.perf_counter() - t0) * 1000, "raw": raw, "error": error})
        msgs = list(messages) + [
            {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False) if raw is not None else ""},
            {"role": "user", "content": f"Deine Antwort war ungültig: {error}\n"
                                        "Korrigiere sie und antworte nur mit dem vollständigen JSON."},
        ]
    raise StructuredError(error, attempts)


def ask(system: str, user: str) -> list[dict]:
    return [{"role": "system", "content": system + " Antworte nur mit JSON."}, {"role": "user", "content": user}]


EXTRACT = "Extrahiere die Angaben aus dem Text in das vorgegebene Format. Datumsangaben als YYYY-MM-DD, Uhrzeiten als HH:MM."


# =====================================================================
# Formats (Pydantic = the spec) and their test tasks
# =====================================================================
class L1Person(BaseModel):
    name: str
    alter: int


class L2Antrag(BaseModel):
    art: Literal["Erholungsurlaub", "Sonderurlaub", "Unbezahlter Urlaub"]
    tage: int
    halbtags: bool
    dringend: bool


class Adresse(BaseModel):
    strasse: str
    plz: str = Field(pattern=r"^\d{5}$")
    ort: str


class L3Besucher(BaseModel):
    name: str
    firma: str
    adresse: Adresse
    termin: date
    uhrzeit: dtime


class Position(BaseModel):
    artikel: str
    menge: int
    einzelpreis: float


class L4Bestellung(BaseModel):
    kunde: str
    positionen: list[Position]
    gesamt: float


class Abwesenheit(BaseModel):
    von: date
    bis: date
    grund: Literal["Urlaub", "Krankheit", "Fortbildung"]


class Mitglied(BaseModel):
    name: str
    rolle: str
    abwesenheiten: list[Abwesenheit]


class Team(BaseModel):
    name: str
    mitglieder: list[Mitglied]


class L5Abteilung(BaseModel):
    name: str
    teams: list[Team]


class Notfallkontakt(BaseModel):
    name: str
    beziehung: str
    telefon: str


class Qualifikation(BaseModel):
    name: str
    seit_jahr: int


class L6Personalakte(BaseModel):
    personalnummer: str = Field(pattern=r"^MA-\d{4}$")
    vorname: str
    nachname: str
    geburtsdatum: date
    eintritt: date
    abteilung: str
    position: str
    vorgesetzter: str | None
    wochenstunden: float
    urlaubstage: int
    vertragsart: Literal["unbefristet", "befristet", "Minijob", "Werkstudent"]
    befristet_bis: date | None
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[a-z]+$")
    telefon: str | None
    sprachen: list[str]
    fuehrerschein: bool
    notfallkontakt: Notfallkontakt
    qualifikationen: list[Qualifikation]


class Schicht(BaseModel):
    typ: Literal["Früh", "Spät", "Nacht"]
    mitarbeiter: list[str]


class Tag(BaseModel):
    datum: date
    wochentag: Literal["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    schichten: list[Schicht]


class L7Wochenplan(BaseModel):
    kalenderwoche: int
    standort: str
    tage: list[Tag]


def _member(d, name):
    return next((m for t in d["teams"] for m in t["mitglieder"] if name.lower() in m["name"].lower()), None)


def _day(d, iso):
    return next((t for t in d["tage"] if t["datum"] == iso), None)


def _shift(day, typ):
    return next((s for s in (day or {}).get("schichten", []) if s["typ"] == typ), None)


# each check: (description, function(dict) -> bool)
FORMAT_CASES = [
    {"level": "L1 flach", "spec": L1Person, "max_tokens": 100,
     "messages": ask(EXTRACT, "Hallo, ich bin Sophie Hoffmann und 34 Jahre alt."),
     "checks": [("name", lambda d: "sophie hoffmann" in d["name"].lower()), ("alter=34", lambda d: d["alter"] == 34)]},

    {"level": "L2 typen", "spec": L2Antrag, "max_tokens": 120,
     "messages": ask(EXTRACT, "Ich möchte 3 Tage Sonderurlaub wegen meiner Hochzeit nehmen, ganze Tage, nicht halbtags. Es eilt nicht."),
     "checks": [("art", lambda d: d["art"] == "Sonderurlaub"), ("tage=3", lambda d: d["tage"] == 3),
                ("halbtags=false", lambda d: d["halbtags"] is False), ("dringend=false", lambda d: d["dringend"] is False)]},

    {"level": "L3 verschachtelt", "spec": L3Besucher, "max_tokens": 250,
     "messages": ask(EXTRACT, "Besuch angekündigt: Herr Markus Braun von der Braun Logistik GmbH, Industriestraße 12, "
                              "50667 Köln. Er kommt am 3. Dezember 2026 um Viertel nach zwei am Nachmittag."),
     "checks": [("plz", lambda d: d["adresse"]["plz"] == "50667"), ("ort", lambda d: "köln" in d["adresse"]["ort"].lower()),
                ("termin", lambda d: d["termin"] == "2026-12-03"), ("uhrzeit 14:15", lambda d: d["uhrzeit"].startswith("14:15"))]},

    {"level": "L4 liste+rechnen", "spec": L4Bestellung, "max_tokens": 400,
     "messages": ask(EXTRACT + " Berechne die Gesamtsumme selbst.",
                     "Bestellung der Muster GmbH: 5 Zeiterfassungsterminals zu je 349,00 €, 20 RFID-Chips zu je "
                     "4,50 € und 1 Wandhalterung für 29,90 €."),
     "checks": [("3 positionen", lambda d: len(d["positionen"]) == 3),
                ("terminal 5×349", lambda d: any(p["menge"] == 5 and abs(p["einzelpreis"] - 349) < 0.01 for p in d["positionen"])),
                ("gesamt 1864,90", lambda d: abs(d["gesamt"] - 1864.90) < 0.01)]},

    {"level": "L5 tief", "spec": L5Abteilung, "max_tokens": 900,
     "messages": ask(EXTRACT,
                     "Abteilung Entwicklung. Team Backend: Jonas Weber (PHP-Entwickler) hat vom 16. bis 20.11.2026 Urlaub; "
                     "Mehmet Yilmaz (Systemadministrator) ist am 1.12.2026 einen Tag auf Fortbildung. Team Frontend: "
                     "Lea Braun (Designerin) hat keine Abwesenheiten; Tim Koch (Entwickler) war vom 9. bis 10.11.2026 krank."),
     "checks": [("2 teams", lambda d: len(d["teams"]) == 2),
                ("4 personen", lambda d: sum(len(t["mitglieder"]) for t in d["teams"]) == 4),
                ("jonas urlaub", lambda d: (m := _member(d, "jonas")) and m["abwesenheiten"]
                 and m["abwesenheiten"][0] == {"von": "2026-11-16", "bis": "2026-11-20", "grund": "Urlaub"}),
                ("mehmet 1 tag", lambda d: (m := _member(d, "mehmet")) and m["abwesenheiten"]
                 and m["abwesenheiten"][0]["von"] == m["abwesenheiten"][0]["bis"] == "2026-12-01"),
                ("lea leer", lambda d: (m := _member(d, "lea")) is not None and m["abwesenheiten"] == []),
                ("tim krank", lambda d: (m := _member(d, "tim")) and m["abwesenheiten"]
                 and m["abwesenheiten"][0]["grund"] == "Krankheit")]},

    {"level": "L6 gross", "spec": L6Personalakte, "max_tokens": 900,
     "messages": ask(EXTRACT + " Fehlende Angaben setzt du auf null.",
                     "Neue Personalakte: Leonie Fischer, Personalnummer MA-1003, geboren am 14.03.1994, seit 1. April 2021 "
                     "bei uns im Support als Support-Mitarbeiterin, Vorgesetzte ist Sophie Hoffmann. Sie arbeitet 30 Stunden "
                     "pro Woche und hat 30 Urlaubstage. Der Vertrag ist befristet bis zum 31.03.2027. E-Mail "
                     "leonie.fischer@personal-planer.de, eine Telefonnummer haben wir noch nicht. Sie spricht Deutsch, "
                     "Englisch und Türkisch und hat einen Führerschein. Notfallkontakt: ihr Bruder Can Fischer, "
                     "0221 555 123. Qualifikationen: ITIL Foundation seit 2022, Erste-Hilfe-Kurs seit 2023."),
     "checks": [("vertrag befristet", lambda d: d["vertragsart"] == "befristet" and d["befristet_bis"] == "2027-03-31"),
                ("telefon null", lambda d: d["telefon"] is None),
                ("geburtsdatum", lambda d: d["geburtsdatum"] == "1994-03-14"),
                ("eintritt", lambda d: d["eintritt"] == "2021-04-01"),
                ("3 sprachen", lambda d: len(d["sprachen"]) == 3),
                ("notfall bruder", lambda d: "bruder" in d["notfallkontakt"]["beziehung"].lower()),
                ("2 qualifikationen", lambda d: len(d["qualifikationen"]) == 2)]},

    {"level": "L7 riesig", "spec": L7Wochenplan, "max_tokens": 2500,
     "messages": ask("Erstelle den Schichtplan im vorgegebenen Format. Datumsangaben als YYYY-MM-DD.",
                     "Schichtplan für KW 47 2026 (Montag 16.11. bis Sonntag 22.11.2026), Standort Köln. Jeden Tag gibt es "
                     "Früh-, Spät- und Nachtschicht. Früh: Anna und Jonas. Spät: Leonie und Mehmet. Nacht: Lukas. "
                     "Ausnahmen: Am Samstag und Sonntag gibt es keine Nachtschicht. Am Mittwoch übernimmt Sophie die "
                     "Frühschicht statt Jonas."),
     "checks": [("7 tage", lambda d: len(d["tage"]) == 7),
                ("daten 16.–22.", lambda d: [t["datum"] for t in d["tage"]] == [f"2026-11-{x}" for x in range(16, 23)]),
                ("wochentage", lambda d: [t["wochentag"] for t in d["tage"]] ==
                 ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]),
                ("19 schichten", lambda d: sum(len(t["schichten"]) for t in d["tage"]) == 19),
                ("wochenende ohne nacht", lambda d: all(_shift(_day(d, x), "Nacht") is None for x in ("2026-11-21", "2026-11-22"))),
                ("mittwoch sophie", lambda d: (s := _shift(_day(d, "2026-11-18"), "Früh")) is not None
                 and "Sophie" in s["mitarbeiter"] and "Jonas" not in s["mitarbeiter"])]},
]


# =====================================================================
# Agent decisions (real agent prompt + tool validation)
# =====================================================================
def _found(mid, name):
    return {"tool": "mitarbeiter_suchen", "args": {"suchbegriff": name}, "status": "ok",
            "result": {"anzahl": 1, "mitarbeiter": [{"id": mid, "name": name}]}}


AGENT_CASES = [
    {"level": "agent: name → suchen", "question": "Wie viel Resturlaub hat Jonas Weber?", "steps": [],
     "tool": "mitarbeiter_suchen", "args": {"suchbegriff": lambda v: "weber" in str(v).lower()}},
    {"level": "agent: ID übernehmen", "question": "Wie viel Resturlaub hat Jonas Weber?",
     "steps": [_found("MA-1002", "Jonas Weber")],
     "tool": "urlaubsanspruch_abfragen", "args": {"mitarbeiter_id": lambda v: v == "MA-1002"}},
    {"level": "agent: nächste Woche Mo–Mi", "question": "Trag für Anna Schneider nächste Woche Montag bis Mittwoch Urlaub ein",
     "steps": [_found("MA-1001", "Anna Schneider")], "tool": "urlaub_beantragen",
     "args": {"von": lambda v: v == NEXT_MON.isoformat(), "bis": lambda v: v == (NEXT_MON + timedelta(days=2)).isoformat()}},
    {"level": "agent: zeiten buchen", "question": "Leonie Fischer hat gestern von 8 bis 16:30 Uhr gearbeitet, 30 Minuten Pause. Bitte buchen.",
     "steps": [_found("MA-1003", "Leonie Fischer")], "tool": "arbeitszeit_buchen",
     "args": {"datum": lambda v: v == (TODAY - timedelta(days=1)).isoformat(),
              "beginn": lambda v: str(v).startswith("08:00"), "ende": lambda v: str(v).startswith("16:30")}},
    {"level": "agent: wissensfrage", "question": "Was kostet der Urlaubsplaner?", "steps": [],
     "tool": "wissen_suchen", "args": {"frage": lambda v: "urlaubsplaner" in str(v).lower()}},
]


# =====================================================================
# Running one task (inside the model's worker process)
# =====================================================================
def run_format_case(model, case) -> dict:
    r = {"json": False, "valid": False, "content": False, "tries": 0, "ms": 0.0, "info": ""}
    try:
        obj, attempts = generate(model, case["messages"], case["spec"], max_tokens=case["max_tokens"])
        r["json"] = r["valid"] = True
    except StructuredError as e:
        attempts, obj = e.attempts, None
        r["json"] = any(a["raw"] is not None for a in attempts)
        r["info"] = f"ungültig: {e}"[:300]
    r["tries"], r["ms"] = len(attempts), sum(a["ms"] for a in attempts)
    if obj is not None:
        d = obj.model_dump(mode="json")
        failed = []
        for name, check in case["checks"]:
            try:
                ok = bool(check(d))
            except Exception:
                ok = False
            if not ok:
                failed.append(name)
        r["content"] = not failed
        r["info"] = (f"falsch: {', '.join(failed)}  →  " if failed else "") + str(d)[:220]
    return r


def run_agent_case(model, case) -> dict:
    from ragbot.agent import prompts
    from ragbot.tools import TOOLS
    r = {"json": False, "valid": False, "content": False, "tries": 1, "ms": 0.0, "info": ""}
    t0 = time.perf_counter()
    try:
        d = model.ask_json(prompts.decide_messages({"question": case["question"], "history": [], "steps": case["steps"]}),
                           prompts.decision_schema())
    except Exception as e:
        r.update(ms=(time.perf_counter() - t0) * 1000, info=f"kein JSON: {e}"[:300])
        return r
    r["ms"], r["json"] = (time.perf_counter() - t0) * 1000, True
    tool = d.get("tool") if d.get("aktion") == "tool" else None
    args = {k: v for k, v in (d.get("args") or {}).items() if v is not None} if isinstance(d.get("args"), dict) else {}
    problems = []
    if tool != case["tool"]:
        problems.append(f"tool={tool}, erwartet {case['tool']}")
    else:
        try:
            TOOLS[tool].params.model_validate(args)
            r["valid"] = True
        except ValidationError as e:
            problems.append("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
        wrong = [f"{k}={args.get(k)!r}" for k, check in case["args"].items() if not check(args.get(k))]
        r["content"] = r["valid"] and not wrong
        problems += [f"falsch {w}" for w in wrong]
    r["info"] = (" | ".join(problems) + "  →  " if problems else "") + f"{tool} {args}"[:220]
    return r


ALL_CASES = [("format", c) for c in FORMAT_CASES] + [("agent", c) for c in AGENT_CASES]


def worker(model_name: str, start: int, out):
    """Runs in its own process: load the model, run tasks from `start`, report each result.
    `out` is a pipe: send() is written immediately, so nothing is lost if the process crashes."""
    from ragbot.llms import get_model
    model = get_model(model_name)
    t0 = time.perf_counter()
    try:
        model.load()
    except Exception as e:
        out.send(("skip", str(e)))
        return
    out.send(("loaded", time.perf_counter() - t0))
    model.ask_json([{"role": "user", "content": "Antworte mit JSON."}],
                   {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})  # warm-up
    for i in range(start, len(ALL_CASES)):
        kind, case = ALL_CASES[i]
        out.send(("start", i))
        r = run_format_case(model, case) if kind == "format" else run_agent_case(model, case)
        out.send(("case", i, r))
    out.send(("end",))


# =====================================================================
# Parent: one process per model, restart after a crash
# =====================================================================
CHECKS = [("json", "JSON"), ("valid", "Valide"), ("content", "Inhalt")]


def print_case(i: int, r: dict):
    level = ALL_CASES[i][1]["level"]
    if r.get("crashed"):
        print(f"  [💥 💥 💥]      –     {level}\n               ABSTURZ des Modellprozesses (exit code {r['exitcode']})", flush=True)
        return
    marks = " ".join("✓" if r[k] else "✗" for k, _ in CHECKS)
    tries = "" if r["tries"] <= 1 else f"  ({r['tries']} Versuche)"
    print(f"  [{marks}] {r['ms']:7.0f} ms  {level}{tries}\n               {r['info']}", flush=True)


def run_model(name: str) -> dict | None:
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}", flush=True)
    ctx = mp.get_context("spawn")             # CUDA needs a fresh process, not a fork
    results, start = {}, 0
    while start < len(ALL_CASES):
        out, child_end = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=worker, args=(name, start, child_end), daemon=True)
        proc.start()
        child_end.close()                     # parent only reads; so recv() notices when the child dies
        current, finished = None, False
        while True:
            try:
                msg = out.recv()
            except EOFError:                  # child is gone (crash) – no more messages
                break
            if msg[0] == "skip":
                print(f"  übersprungen: {msg[1]}")
                proc.join()
                return None
            if msg[0] == "loaded":
                print(f"  geladen in {msg[1]:.1f}s   (heute: {TODAY}, nächster Montag: {NEXT_MON})", flush=True)
            elif msg[0] == "start":
                current = msg[1]
            elif msg[0] == "case":
                results[msg[1]] = msg[2]
                print_case(msg[1], msg[2])
                current = None
            elif msg[0] == "end":
                finished = True
                break
        proc.join(timeout=30)
        if finished:
            break
        crashed = current if current is not None else start
        results[crashed] = {"crashed": True, "exitcode": proc.exitcode, "json": False, "valid": False,
                            "content": False, "tries": 0, "ms": 0.0}
        print_case(crashed, results[crashed])
        print("  → Modell wird neu geladen, weiter mit der nächsten Aufgabe …", flush=True)
        start = crashed + 1

    done = [r for r in results.values() if not r.get("crashed")]
    return {"model": name, "n": len(ALL_CASES), "crashes": sum(1 for r in results.values() if r.get("crashed")),
            "first_try": sum(1 for r in done if r["valid"] and r["tries"] == 1),
            "ms": statistics.mean(r["ms"] for r in done) if done else 0.0,
            **{k: sum(1 for r in done if r[k]) for k, _ in CHECKS},
            "levels": {ALL_CASES[i][1]["level"]: ("💥" if r.get("crashed") else "✓" if r["content"] else "✗")
                       for i, r in sorted(results.items())}}


def print_summary(rows: list[dict]):
    print(f"\n{'=' * 78}\nVERGLEICH\n{'=' * 78}")
    print(f"  {'Modell':20} {'JSON':>7} {'Valide':>7} {'Inhalt':>7} {'1.Versuch':>10} {'Abstürze':>9} {'Ø Zeit':>9}")
    for r in sorted(rows, key=lambda r: (-r["content"], -r["valid"], r["ms"])):
        print(f"  {r['model']:20} {r['json']:>3}/{r['n']:<3} {r['valid']:>3}/{r['n']:<3} {r['content']:>3}/{r['n']:<3}"
              f" {r['first_try']:>6}/{r['n']:<3} {r['crashes']:>9} {r['ms']:>7.0f}ms")
    print(f"\n  Inhalt je Stufe:")
    levels = [c["level"] for _, c in ALL_CASES]
    print(f"  {'':28}" + "".join(f"{r['model'][:14]:>16}" for r in rows))
    for lv in levels:
        print(f"  {lv:28}" + "".join(f"{r['levels'].get(lv, '–'):>16}" for r in rows))


if __name__ == "__main__":
    from ragbot.llms import available
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", help=f"Modelle (Standard: alle aus registry.py): {', '.join(available())}")
    rows = [r for name in (ap.parse_args().models or available()) if (r := run_model(name))]
    if rows:
        print_summary(rows)