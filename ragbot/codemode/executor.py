"""Runs LLM-generated Python safely: check first, then execute in a separate process.

    from ragbot.codemode.executor import run
    r = run('print(len(erp.mitarbeiter.liste()))')
    r -> {"ok": True, "output": "38\n", "error": None, "plan": [], "ms": 41.0}
    "plan" holds the changes the script planned with the SDK's write methods (nothing is written here).

Three layers of protection:
  1. check()  – reads the code as a syntax tree (AST) BEFORE running it and rejects
                imports (except datetime/collections/math/statistics), dunder tricks
                (__class__ …) and dangerous functions (open, eval …).
  2. sandbox  – the code only sees `erp`, `heute`, a few helpers and safe builtins.
  3. process  – runs in its own process with a time limit; the ERP database is
                opened read-only by the SDK, so nothing can be changed.
"""
import ast
import builtins
import multiprocessing as mp
import time
import traceback

MAX_OUTPUT = 20_000                 # characters of print output we keep
TIMEOUT_S = 15

FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "input", "breakpoint", "exit", "quit", "help",
                   "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr", "__import__",
                   "memoryview", "type", "object", "super", "classmethod", "staticmethod", "property"}

SAFE_BUILTINS = {name: getattr(builtins, name) for name in [
    "len", "sum", "min", "max", "sorted", "reversed", "range", "enumerate", "zip", "map", "filter",
    "any", "all", "abs", "round", "int", "float", "str", "bool", "list", "dict", "set", "tuple",
    "isinstance", "Exception", "ValueError", "KeyError"]}


# harmless standard modules the model may import out of habit (they are pre-loaded anyway)
SAFE_MODULES = {"datetime", "collections", "math", "statistics"}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in SAFE_MODULES or level:
        raise ImportError(f"import {name} ist nicht erlaubt.")
    return __import__(name, globals, locals, fromlist, level)


class UnsafeCode(Exception):
    pass


def check(code: str) -> None:
    """Raise UnsafeCode (or SyntaxError) if the code uses anything we don't allow."""
    tree = ast.parse(code, filename="<script>")
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in SAFE_MODULES]
        elif isinstance(node, ast.ImportFrom):
            bad = [node.module or "."] if node.module not in SAFE_MODULES or node.level else []
        else:
            bad = []
        if bad:
            raise UnsafeCode(f"Zeile {line}: import {bad[0]} ist nicht erlaubt. Verfügbar sind nur erp, heute, "
                             f"date, timedelta, Counter, defaultdict und die Module {', '.join(sorted(SAFE_MODULES))}.")
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef, ast.Await,
                             ast.With, ast.AsyncWith)):
            raise UnsafeCode(f"Zeile {line}: {type(node).__name__} ist nicht erlaubt.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise UnsafeCode(f"Zeile {line}: Zugriff auf '{node.attr}' ist nicht erlaubt.")
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            raise UnsafeCode(f"Zeile {line}: '{node.id}' ist nicht erlaubt.")


def _child(code: str, conn):
    """Runs in the separate process."""
    from collections import Counter, defaultdict
    from datetime import date, timedelta

    from ragbot.codemode.sdk import PLAN, erp

    out, size = [], 0

    def _print(*args, sep=" ", end="\n"):
        nonlocal size
        text = sep.join(str(a) for a in args) + end
        if size < MAX_OUTPUT:
            out.append(text[:MAX_OUTPUT - size])
        size += len(text)

    env = {"__builtins__": {**SAFE_BUILTINS, "print": _print, "__import__": _safe_import},
           "erp": erp, "heute": date.today().isoformat(),
           "date": date, "timedelta": timedelta, "Counter": Counter, "defaultdict": defaultdict}
    t0 = time.perf_counter()
    try:
        exec(compile(code, "<script>", "exec"), env)
        error = None
    except Exception as e:
        # keep only the lines that point into the script, so the LLM sees where it went wrong
        tb = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "<script>"]
        where = f"Zeile {tb[-1].lineno}: " if tb else ""
        error = f"{where}{type(e).__name__}: {e}"
    if size > MAX_OUTPUT:
        out.append(f"\n… Ausgabe gekürzt ({size} Zeichen insgesamt).")
    conn.send({"output": "".join(out), "error": error, "plan": list(PLAN),
               "ms": round((time.perf_counter() - t0) * 1000, 1)})
    conn.close()


def run(code: str, timeout: float = TIMEOUT_S) -> dict:
    """Check and execute. Never raises: returns {"ok", "output", "error", "plan", "ms"}."""
    try:
        check(code)
    except (UnsafeCode, SyntaxError) as e:
        msg = f"Syntaxfehler in Zeile {e.lineno}: {e.msg}" if isinstance(e, SyntaxError) else str(e)
        return {"ok": False, "output": "", "error": msg, "plan": [], "ms": 0.0}

    ctx = mp.get_context("spawn")              # fresh process: no access to the server's memory or the GPU
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(code, child), daemon=True)
    proc.start()
    child.close()
    if parent.poll(timeout):
        try:
            res = parent.recv()
        except EOFError:
            res = {"output": "", "error": "Der Prozess ist abgestürzt.", "plan": [], "ms": 0.0}
    else:
        res = {"output": "", "error": f"Zeitlimit von {timeout:.0f} s überschritten (Endlosschleife?).", "plan": [],
               "ms": timeout * 1000}
    if proc.is_alive():
        proc.kill()
    proc.join(1)
    return {"ok": res["error"] is None, **res}