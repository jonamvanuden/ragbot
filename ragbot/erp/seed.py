"""Fill the mock ERP (data/erp.db) with a fictional company: ~40 people, 6 departments, one year of data.

    python -m ragbot.erp.seed          # deletes erp.db and builds it again

Deterministic: the same seed gives the same company (data up to "today" grows with the date).
The 8 people the tools and tests already know (MA-1001 … MA-1008) keep their IDs and names.
All people and companies are invented.
"""
import random
from datetime import date, timedelta

from ragbot import config
from ragbot.erp.db import connect

SEED = 42
YEAR = 2026
STANDORT = "Euskirchen"

# public holidays NRW 2026 – no work, no vacation days counted
FEIERTAGE = {date(2026, m, d) for m, d in [(1, 1), (4, 3), (4, 6), (5, 1), (5, 14), (5, 25), (6, 4),
                                           (10, 3), (11, 1), (12, 25), (12, 26)]}

ABTEILUNGEN = [  # id, name, roles for normal staff
    ("ABT-01", "Vertrieb", ["Key Account Manager", "Vertriebsmitarbeiter", "Vertriebsinnendienst"]),
    ("ABT-02", "Entwicklung", ["PHP-Entwickler", "Frontend-Entwickler", "Softwaretester"]),
    ("ABT-03", "Support", ["Support-Mitarbeiter", "Support-Mitarbeiter", "Werkstudent"]),
    ("ABT-04", "IT-Betrieb", ["Systemadministrator", "Netzwerkadministrator"]),
    ("ABT-05", "Personal", ["Personalreferent", "Lohnbuchhalter"]),
    ("ABT-06", "Buchhaltung", ["Finanzbuchhalter", "Controller"]),
]

# the people the agent tests already know: id, first, last, department, role, manager, days, hours
BEKANNT = [
    ("MA-1001", "Anna", "Schneider", "ABT-01", "Key Account Managerin", "MA-1005", 30, 40),
    ("MA-1002", "Jonas", "Weber", "ABT-02", "PHP-Entwickler", "MA-1006", 28, 40),
    ("MA-1003", "Leonie", "Fischer", "ABT-03", "Support-Mitarbeiterin", "MA-1007", 30, 30),
    ("MA-1004", "Mehmet", "Yilmaz", "ABT-04", "Systemadministrator", "MA-1009", 30, 40),
    ("MA-1005", "Claudia", "Becker", "ABT-01", "Vertriebsleiterin", None, 30, 40),
    ("MA-1006", "Thomas", "Wagner", "ABT-02", "Teamleiter Entwicklung", None, 30, 40),
    ("MA-1007", "Sophie", "Hoffmann", "ABT-03", "Teamleiterin Support", None, 30, 40),
    ("MA-1008", "Lukas", "Schäfer", "ABT-03", "Werkstudent", "MA-1007", 15, 20),
    ("MA-1009", "Stefan", "Braun", "ABT-04", "Leiter IT-Betrieb", None, 30, 40),
    ("MA-1010", "Katrin", "Neumann", "ABT-05", "Personalleiterin", None, 30, 40),
    ("MA-1011", "Michael", "Richter", "ABT-06", "Leiter Buchhaltung", None, 30, 40),
]
LEITUNG = {"ABT-01": "MA-1005", "ABT-02": "MA-1006", "ABT-03": "MA-1007",
           "ABT-04": "MA-1009", "ABT-05": "MA-1010", "ABT-06": "MA-1011"}

VORNAMEN = ["Lena", "Paul", "Mia", "Felix", "Emma", "Jan", "Laura", "Tim", "Sarah", "David", "Julia", "Niklas",
            "Hannah", "Moritz", "Lisa", "Florian", "Nina", "Sebastian", "Marie", "Kevin", "Elif", "Ahmet",
            "Olga", "Piotr", "Sandra", "Markus", "Jana", "Tobias", "Carina", "Daniel"]
NACHNAMEN = ["Müller", "Schmitz", "Klein", "Wolf", "Krüger", "Hartmann", "Lange", "Werner", "Krause", "Meier",
             "Lehmann", "Köhler", "Walter", "König", "Kaiser", "Fuchs", "Peters", "Scholz", "Möller", "Weiß",
             "Jung", "Hahn", "Vogel", "Friedrich", "Keller", "Günther", "Frank", "Berger", "Winkler", "Roth"]
FIRMEN = ["Muster GmbH", "Braun Logistik GmbH", "Rheinland Bau AG", "Eifel Metall KG", "Nordwind IT GmbH",
          "Sonnenhof Pflege gGmbH", "Kessler & Söhne", "Datenwerk Köln GmbH"]
AUFGABEN = ["Terminal im Lager prüfen", "Urlaubsplanung Q1 abstimmen", "Kundenschulung vorbereiten",
            "Backup-Konzept aktualisieren", "Angebot für Muster GmbH erstellen", "Lizenzen verlängern",
            "Onboarding neuer Kollegin", "Release 5.9 testen", "Monatsabschluss vorbereiten",
            "Hilfeartikel zur Zeiterfassung überarbeiten", "Server-Updates einspielen", "Reisekosten prüfen"]


def workdays(start: date, end: date) -> list[date]:
    """Mon–Fri between start and end (inclusive), without public holidays."""
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5 and d not in FEIERTAGE]


def make_people(rnd: random.Random) -> list[dict]:
    people = [{"id": i, "vorname": v, "nachname": n, "abteilung_id": a, "rolle": r, "vorgesetzt_id": boss,
               "urlaubstage": days, "wochenstunden": hours} for i, v, n, a, r, boss, days, hours in BEKANNT]
    used = {(p["vorname"], p["nachname"]) for p in people}
    plan = {"ABT-01": 5, "ABT-02": 7, "ABT-03": 5, "ABT-04": 3, "ABT-05": 3, "ABT-06": 4}   # extra people
    nr = 1012
    for abt_id, roles in ((a, r) for a, _, r in ABTEILUNGEN):
        for _ in range(plan[abt_id]):
            while (name := (rnd.choice(VORNAMEN), rnd.choice(NACHNAMEN))) in used:
                pass
            used.add(name)
            role = rnd.choice(roles)
            student = role == "Werkstudent"
            people.append({"id": f"MA-{nr}", "vorname": name[0], "nachname": name[1], "abteilung_id": abt_id,
                           "rolle": role, "vorgesetzt_id": LEITUNG[abt_id],
                           "urlaubstage": 15 if student else rnd.choice([28, 30, 30]),
                           "wochenstunden": 20 if student else rnd.choice([40, 40, 40, 32, 30])})
            nr += 1
    for p in people:
        p["email"] = f"{p['vorname'][0].lower()}.{p['nachname'].lower()}@example-firma.de" \
            .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        p["eintritt"] = date(rnd.randint(2012, 2025), rnd.randint(1, 12), 1).isoformat()
    return people


def make_vacations(rnd, people, today):
    rows, nr = [], 1
    for p in people:
        budget = p["urlaubstage"]
        taken: set[date] = set()
        starts = [date(YEAR, 1, 2) + timedelta(days=rnd.randint(0, 350)) for _ in range(rnd.randint(2, 4))]
        if rnd.random() < 0.5:                                   # half the people have something coming up soon
            starts.append(today + timedelta(days=rnd.randint(3, 40)))
        for start in starts:                                     # 2–5 vacation blocks per person
            days = workdays(start, start + timedelta(days=rnd.choice([2, 4, 7, 11, 14])))
            days = [d for d in days if d.year == YEAR]
            if not days or len(days) > budget or taken & set(days):
                continue
            taken |= set(days)
            budget -= len(days)
            beantragt = days[0] - timedelta(days=rnd.randint(14, 60))
            if days[0] < today:
                status = rnd.choices(["genehmigt", "storniert"], [95, 5])[0]
            else:
                status = rnd.choices(["beantragt", "genehmigt", "abgelehnt"], [40, 50, 10])[0]
            decided = status in ("genehmigt", "abgelehnt", "storniert")
            rows.append({"id": f"UA-{nr:05d}", "mitarbeiter_id": p["id"], "von": days[0].isoformat(),
                         "bis": days[-1].isoformat(), "arbeitstage": len(days), "art": "Erholungsurlaub",
                         "status": status, "kommentar": None, "beantragt_am": min(beantragt, today).isoformat(),
                         "entschieden_von": LEITUNG[p["abteilung_id"]] if decided and status != "storniert" else None,
                         "entschieden_am": (beantragt + timedelta(days=rnd.randint(1, 5))).isoformat() if decided else None,
                         "begruendung": "Personelle Engpässe im Zeitraum" if status == "abgelehnt" else None})
            nr += 1
        if rnd.random() < 0.15:                                  # occasional special leave (wedding, move …)
            d = date(YEAR, 1, 2) + timedelta(days=rnd.randint(0, 300))
            days = [x for x in workdays(d, d + timedelta(days=1)) if x not in taken]
            if days:
                rows.append({"id": f"UA-{nr:05d}", "mitarbeiter_id": p["id"], "von": days[0].isoformat(),
                             "bis": days[-1].isoformat(), "arbeitstage": len(days), "art": "Sonderurlaub",
                             "status": "genehmigt", "kommentar": rnd.choice(["Umzug", "Hochzeit", "Geburt eines Kindes"]),
                             "beantragt_am": (days[0] - timedelta(days=20)).isoformat(),
                             "entschieden_von": LEITUNG[p["abteilung_id"]],
                             "entschieden_am": (days[0] - timedelta(days=18)).isoformat(), "begruendung": None})
                nr += 1
    return rows


def make_sick(rnd, people, today, blocked):
    rows, nr = [], 1
    for p in people:
        for _ in range(rnd.choices([0, 1, 2, 3, 4, 6], [15, 25, 25, 18, 10, 7])[0]):
            start = date(YEAR, 1, 2) + timedelta(days=rnd.randint(0, max((today - date(YEAR, 1, 2)).days - 5, 1)))
            days = [d for d in workdays(start, start + timedelta(days=rnd.choice([0, 1, 2, 4, 6, 9]))) if d < today]
            if not days or set(days) & blocked[p["id"]]:
                continue
            blocked[p["id"]] |= set(days)
            rows.append({"id": f"KM-{nr:05d}", "mitarbeiter_id": p["id"], "von": days[0].isoformat(),
                         "bis": days[-1].isoformat(), "arbeitstage": len(days),
                         "au_bescheinigung": int(len(days) > 3)})          # doctor's note from day 4
            nr += 1
    return rows


def make_bookings(rnd, people, today, blocked):
    rows = []
    for p in people:
        daily = p["wochenstunden"] / 5
        days_per_week = 3 if p["wochenstunden"] <= 20 else 5            # students work 3 longer days
        for d in workdays(date(YEAR, 1, 1), today - timedelta(days=1)):
            if d in blocked[p["id"]] or (days_per_week == 3 and d.weekday() in (1, 3)):
                continue
            hours = (daily * 5 / days_per_week) + rnd.gauss(0.2, 0.6)    # slightly more than planned on average
            pause = 45 if hours > 9 else 30 if hours > 6 else 0
            start = 7 * 60 + rnd.randint(0, 150)                         # 07:00 – 09:30
            end = start + round(hours * 60) + pause
            rows.append({"mitarbeiter_id": p["id"], "datum": d.isoformat(),
                         "beginn": f"{start // 60:02d}:{start % 60:02d}", "ende": f"{end // 60:02d}:{end % 60:02d}",
                         "pause_minuten": pause, "netto_stunden": round(hours, 2),
                         "quelle": "manuell" if rnd.random() < 0.05 else "terminal"})
    return rows


def make_shifts(rnd, people, today, blocked):
    """Support works in shifts: early and late shift on workdays, four weeks back and eight ahead."""
    support = [p["id"] for p in people if p["abteilung_id"] == "ABT-03"]
    rows = []
    for d in workdays(today - timedelta(days=28), today + timedelta(days=56)):
        free = [m for m in support if d not in blocked[m]]
        rnd.shuffle(free)
        for typ, people_needed in (("Früh", 2), ("Spät", 2)):
            for m in free[:people_needed]:
                rows.append({"datum": d.isoformat(), "typ": typ, "mitarbeiter_id": m, "standort": STANDORT})
            free = free[people_needed:]
    return rows


def make_visitors(rnd, people, today):
    rows = []
    for nr in range(1, 21):
        d = today + timedelta(days=rnd.randint(-30, 30))
        while d.weekday() >= 5:
            d += timedelta(days=1)
        status = "eingetroffen" if d < today else rnd.choices(["angemeldet", "abgesagt"], [85, 15])[0]
        rows.append({"id": f"BS-{nr:05d}", "name": f"{rnd.choice(['Herr', 'Frau'])} {rnd.choice(VORNAMEN)} {rnd.choice(NACHNAMEN)}",
                     "firma": rnd.choice(FIRMEN), "gastgeber_id": rnd.choice(people)["id"], "datum": d.isoformat(),
                     "uhrzeit": f"{rnd.randint(8, 16):02d}:{rnd.choice(['00', '15', '30', '45'])}", "status": status})
    return rows


def make_tasks(rnd, people, today):
    rows = []
    for nr in range(1, 31):
        p = rnd.choice(people)
        due = today + timedelta(days=rnd.randint(-20, 40))
        status = rnd.choices(["offen", "in Arbeit", "erledigt"], [40, 25, 35])[0]
        rows.append({"id": f"AU-{nr:05d}", "titel": rnd.choice(AUFGABEN), "beschreibung": None,
                     "zugewiesen_id": p["id"], "erstellt_von": LEITUNG[p["abteilung_id"]], "faellig": due.isoformat(),
                     "prioritaet": rnd.choices(["niedrig", "normal", "hoch"], [25, 55, 20])[0], "status": status})
    return rows


def insert(conn, table: str, rows: list[dict]):
    if rows:
        cols = list(rows[0])
        conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [[r[c] for c in cols] for r in rows])


def main():
    rnd = random.Random(SEED)
    today = date.today()
    config.ERP_DB.unlink(missing_ok=True)                 # start from scratch
    conn = connect()

    people = make_people(rnd)
    vacations = make_vacations(rnd, people, today)
    blocked = {p["id"]: set() for p in people}            # days off: no work bookings, no shifts
    for v in vacations:
        if v["status"] == "genehmigt":
            blocked[v["mitarbeiter_id"]] |= set(workdays(date.fromisoformat(v["von"]), date.fromisoformat(v["bis"])))
    sick = make_sick(rnd, people, today, blocked)

    with conn:                                            # one transaction: all or nothing
        conn.execute("PRAGMA defer_foreign_keys = ON")    # managers and departments point at each other
        insert(conn, "abteilungen", [{"id": a, "name": n, "leitung_id": LEITUNG[a]} for a, n, _ in ABTEILUNGEN])
        insert(conn, "mitarbeiter", [{k: p[k] for k in ("id", "vorname", "nachname", "email", "abteilung_id", "rolle",
                                                        "vorgesetzt_id", "eintritt", "wochenstunden", "urlaubstage")}
                                     for p in people])
        insert(conn, "urlaubsantraege", vacations)
        insert(conn, "krankmeldungen", sick)
        insert(conn, "zeitbuchungen", make_bookings(rnd, people, today, blocked))
        insert(conn, "schichten", make_shifts(rnd, people, today, blocked))
        insert(conn, "besucher", make_visitors(rnd, people, today))
        insert(conn, "aufgaben", make_tasks(rnd, people, today))

    print(f"erp.db neu erstellt: {config.ERP_DB}  (Stand {today})")
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        print(f"  {table:16} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>6} Zeilen")
    conn.close()


if __name__ == "__main__":
    main()