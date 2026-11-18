from ragbot.codemode.executor import run

tests = {
    "ok": "t = Counter()\nfor k in erp.krank.meldungen(von='2026-01-01', bis='2026-12-31'):\n    t[k['name']] += k['arbeitstage']\nprint(t.most_common(3))",
    "import": "import os\nprint(os.listdir('/'))",
    "dunder": "print(().__class__.__bases__[0].__subclasses__())",
    "open": "print(open('/etc/passwd').read())",
    "loop": "while True:\n    pass",
    "typo": "erp.mitarbeiter.alle()",
}

if __name__ == "__main__":
    for name, code in tests.items():
        r = run(code, timeout=5)
        print(f"{name:7} ok={r['ok']!s:5} {r['error'] or r['output'][:80]}")