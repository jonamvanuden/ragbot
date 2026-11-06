"""Talk to the agent in the terminal. Write actions ask for your OK first.

    python -m ragbot.agent

Stop the web server first: the LLM and the knowledge base can only be open once.
"""
import json
import uuid

from ragbot.agent.graph import resume, start
from ragbot.llms import get_model


def short(obj, n=160) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + " …"


def main():
    model = get_model()
    print(f"Lade {model.name} …")
    model.load()
    session = f"cli-{uuid.uuid4().hex[:8]}"
    history: list[dict] = []
    print("Agent bereit. Beispiele: 'Wie viel Resturlaub hat Jonas Weber?', "
          "'Trag für Anna Schneider nächste Woche Montag bis Mittwoch Urlaub ein'. 'exit' beendet.")

    while True:
        try:
            q = input("\nDu: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            break

        events, answer = start(q, history, session), ""
        while True:
            pending = None
            for ev in events:
                t = ev["type"]
                if t == "thought":
                    print(f"  💭 {ev['text']}")
                elif t == "tool_start":
                    print(f"  🔧 {ev['tool']} {short(ev['args'])}")
                elif t == "tool_result":
                    print(f"     → {ev['status']}: {short(ev.get('result') or ev.get('error'))}")
                elif t == "rejected":
                    print("     → abgelehnt, nicht ausgeführt")
                elif t == "answer":
                    if not answer:
                        print("\nAgent: ", end="")
                    print(ev["text"], end="", flush=True)
                    answer += ev["text"]
                elif t == "approval":
                    pending = ev
            if not pending:
                break
            print(f"\n  ⚠  {pending['tool']} ({pending['product']}) möchte Daten ändern:")
            print("     " + json.dumps(pending["args"], ensure_ascii=False, indent=2).replace("\n", "\n     "))
            ok = input("     Ausführen? [j/N] ").strip().lower() in {"j", "ja", "y", "yes"}
            events = resume(pending["thread_id"], ok)

        print()
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": answer}]

    print("Tschüss!")


if __name__ == "__main__":
    main()
