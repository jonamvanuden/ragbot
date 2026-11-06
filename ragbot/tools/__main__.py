"""Try the tools from the terminal.

    python -m ragbot.tools                          # list all tools
    python -m ragbot.tools schema urlaub_beantragen # show the JSON schema of one tool
    python -m ragbot.tools call mitarbeiter_suchen '{"suchbegriff": "Weber"}'
    python -m ragbot.tools demo                     # run a small end-to-end scenario
    python -m ragbot.tools log                      # last 20 entries of api_calls
"""
import json
import sys
from datetime import date, timedelta

from ragbot.tools import TOOLS, call_tool
from ragbot.tools import store


def show(out: dict):
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


def demo():
    monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)   # next Monday
    steps = [
        ("mitarbeiter_suchen", {"suchbegriff": "Weber"}),
        ("urlaubsanspruch_abfragen", {"mitarbeiter_id": "MA-1002"}),
        ("urlaub_beantragen", {"mitarbeiter_id": "MA-1002", "von": str(monday), "bis": str(monday + timedelta(days=4))}),
        ("urlaubsanspruch_abfragen", {"mitarbeiter_id": "MA-1002"}),
        ("schicht_zuweisen", {"mitarbeiter_id": "MA-1002", "datum": str(monday + timedelta(days=2)), "schicht": "Frühschicht"}),
        ("abwesenheiten_abfragen", {"von": str(monday), "bis": str(monday + timedelta(days=6)), "abteilung": "Entwicklung"}),
        ("urlaub_beantragen", {"mitarbeiter_id": "MA-1002", "von": "2026-13-01", "bis": "2026-12-05"}),
    ]
    for name, args in steps:
        print(f"\n▶ {name} {json.dumps(args, ensure_ascii=False)}")
        out = call_tool(name, args, caller="cli-demo")
        if name == "urlaub_beantragen" and out["status"] == "ok":
            antrag = out["result"]["antrag_id"]
            show(out)
            print(f"\n▶ urlaubsantrag_entscheiden {antrag} → genehmigt")
            out = call_tool("urlaubsantrag_entscheiden", {"antrag_id": antrag, "entscheidung": "genehmigt"}, caller="cli-demo")
        show(out)


def main(argv):
    if not argv:
        print(f"{len(TOOLS)} Tools:\n")
        for t in TOOLS.values():
            first_line = t.description.splitlines()[0]
            print(f"  {t.kind:5}  {t.name:26} [{t.product}]\n         {first_line}")
        return
    cmd, *rest = argv
    if cmd == "schema":
        show(TOOLS[rest[0]].schema())
    elif cmd == "call":
        show(call_tool(rest[0], json.loads(rest[1]) if len(rest) > 1 else {}, caller="cli"))
    elif cmd == "demo":
        demo()
    elif cmd == "log":
        for r in reversed(store.recent(20)):
            print(f"#{r['id']:<4} {r['ts'][11:19]}  {r['status']:8} {r['kind']:5} {r['tool']:26} {r['params']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
