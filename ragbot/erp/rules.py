"""Business rules of the mock ERP that more than one part of the app needs.

They live here once, so the toolbox (ragbot/tools) and the code-mode SDK (ragbot/codemode)
always calculate the same way. Each function gets a query function `q(sql, *args) -> list[dict]`,
so the caller decides how the database is opened (read-write for tools, read-only for the SDK).
"""
from datetime import date, timedelta

from ragbot.erp.seed import workdays


def zeitkonto(q, mitarbeiter_id: str, wochenstunden: float, monat: str) -> dict:
    """Target/actual hours and balance for one month ("YYYY-MM").
    Counts only days that are already over; approved vacation and sick days reduce the target."""
    y, mo = map(int, monat.split("-"))
    first = date(y, mo, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    bis = min(last, date.today() - timedelta(days=1))
    if bis < first:
        return {"monat": monat, "zeitraum": None, "soll_stunden": 0.0, "ist_stunden": 0.0, "saldo_stunden": 0.0,
                "gebuchte_tage": 0, "abwesenheitstage": 0, "davon_manuell_gebucht": 0.0}

    frei = set()
    for r in q("""SELECT von, bis FROM urlaubsantraege
                  WHERE mitarbeiter_id = ? AND status = 'genehmigt' AND von <= ? AND bis >= ?
                  UNION ALL
                  SELECT von, bis FROM krankmeldungen
                  WHERE mitarbeiter_id = ? AND von <= ? AND bis >= ?""",
               mitarbeiter_id, bis.isoformat(), first.isoformat(), mitarbeiter_id, bis.isoformat(), first.isoformat()):
        frei |= set(workdays(date.fromisoformat(r["von"]), date.fromisoformat(r["bis"])))
    arbeitstage = set(workdays(first, bis))
    soll = round(len(arbeitstage - frei) * wochenstunden / 5, 2)

    b = q("""SELECT COUNT(*) AS tage, COALESCE(SUM(netto_stunden), 0) AS ist,
                    COALESCE(SUM(CASE WHEN quelle = 'manuell' THEN netto_stunden END), 0) AS manuell
             FROM zeitbuchungen WHERE mitarbeiter_id = ? AND datum BETWEEN ? AND ?""",
          mitarbeiter_id, first.isoformat(), bis.isoformat())[0]
    ist = round(b["ist"], 2)
    return {"monat": monat, "zeitraum": f"{first} bis {bis}", "soll_stunden": soll, "ist_stunden": ist,
            "saldo_stunden": round(ist - soll, 2), "gebuchte_tage": b["tage"],
            "abwesenheitstage": len(arbeitstage & frei), "davon_manuell_gebucht": round(b["manuell"], 2)}