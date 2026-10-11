"""Self-check for the booking-page parsers, on trimmed copies of the real pages.

Run with:  uv run python tests/test_reservation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import date, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from core.booking import TZ, planned_points  # noqa: E402
from core.exceptions import BookingError, NotOpenYet  # noqa: E402
from core.reservation import (  # noqa: E402
    find_link, find_timeslot, parse_account, parse_overview, parse_seats, reserve_seat, select_seat,
)


def _section(title, rows):
    body = "".join(
        f'<tr><td><nobr>von <strong>{start}</strong> bis</nobr></td><td>'
        + "&nbsp;| ".join(f'<nobr><a href="?mod=020&locslot_id={lid}">{end}</a></nobr>' for end, lid in ends)
        + "</td></tr>"
        for start, ends in rows
    )
    return (f"<h2>{title}</h2><!--008--><table><thead><tr><th colspan='2'>Zeitslots</th></tr>"
            f"</thead><tbody>{body}</tbody></table>")


SLOTS = "<main><div><h1>Einzelarbeitsplätze: Montag, 12.10.2026</h1>" + "".join([
    _section("Zentralbibliothek / Hauptlesesaal im 1. OG (Einzelarbeitsplätze)",
             [("08:00", [("09:00", 1), ("12:00", 2)]), ("09:00", [("12:00", 3)])]),
    # booked out: no table, so the next library's table must not be taken for it
    '<h2>Bibliotheken im Fürstenberghaus / Einzelarbeitsplätze</h2><p style="color:red;">Ausgebucht</p>',
    _section("Zentralbibliothek / Westfalica-Lesesaal (Einzelarbeitsplätze)", [("08:00", [("12:00", 4)])]),
    _section("Medizin-Bibliothek / Einzelarbeitsplätze", [("08:00", [("12:00", 5)])]),
]) + "</div></main>"

SEATS = """<main><table><tbody>
<tr><td>Platzkategorie</td><td>Gruppenarbeitsräume (separiert)</td></tr>
<tr><td>Bibliothek / Platzgruppe</td><td>Zentralbibliothek<!--015-->&nbsp;/ <!--/015-->Arbeitskabinen<br /><em>Adresse: Krummer Timpen 3</em><br />&#10140;&nbsp;<a href="https://www.ulb.uni-muenster.de/sitzplan" target="_blank">Sitzplan</a></td></tr>
<tr><td>Zeitslot</td><td>08:00<!--017-->&ndash;<!--/017-->12:00 Uhr</td></tr>
<tr><td>Verfügbare Plätze</td><td>3 Plätze:<br /><br />""" + "".join(
    f'<!--031--><a href="?mod=030&locslot_id=9&seat_id={i}&onetime_token=t">{name}</a>{note}<!--/031--><br />'
    for i, (name, note) in enumerate([("Kabine G2", " (max. 8 Personen)"), ("Kabine G3", ""), ("Platz 018", "")])
) + "</td></tr></tbody></table></main>"

OVERVIEW = """<main><h1>Reservierung</h1>
<h2>Freitag, 09.10.2026</h2>
<p><strong>Einzelarbeitsplätze</strong></p><p><span>&#x25AE;</span> geringe Auslastung (0.2 %)<br /><em>Last-Minute-Buchung</em><br /><a href="?mod=010&cat_id=1&date=2026-10-09">Bibliothek und Zeitslot auswählen</a></p>
<p><strong>Gruppenarbeitsräume (separiert)</strong></p><p>mittlere Auslastung (52.9 %)<br /><a href="?mod=010&cat_id=4&date=2026-10-09">Bibliothek und Zeitslot auswählen</a></p>
<h2>Dienstag, 13.10.2026</h2>
<p>Reservierung möglich ab Samstag, 10.10.2026, 09:00</p>
</main>"""

ACCOUNT = """<main><h1>Konto</h1>
<h2>Guthaben</h2><table><thead><tr><th>Woche</th><th>Anzahl</th><th>Punkte</th></tr></thead><tbody>
<tr><td><nobr>Mo, 05.10.2026 &ndash;</nobr> So, 11.10.2026</td><td>5</td><td>1.800</td></tr></tbody></table>
<h2>Aktuelle Reservierung</h2><p>Keine Reservierung gefunden.</p>
<h2>Zukünftige Reservierungen</h2><table><thead><tr><th>Nr.</th><th>Reservierungen</th></tr></thead><tbody>
<tr><td>1</td><td><strong>Fr, 09.10.2026, 21:00<!--017-->&ndash;<!--/017-->22:00</strong><br />
Zentralbibliothek / Hauptlesesaal / Platz 302<br />
<em>Eingesetzte Punkte:</em> 0 Punkte<br />
<a href="?mod=100&res_id=1&onetime_token=t">Reservierung stornieren</a>
<a href="?mod=200&res_id=1&onetime_token=t">Platz umtauschen</a>
</td></tr></tbody></table></main>"""

BOOKED = """<main><p><span>Erfolg:</span> Die Reservierung wurde erfolgreich eingetragen.</p><table><tbody>
<tr style="background-color:yellow;"><td>1</td><td><strong>Mo, 12.10.2026, 08:00<!--017-->&ndash;<!--/017-->12:00</strong><br />
Zentralbibliothek / Hauptlesesaal im 1. OG (Einzelarbeitsplätze) / Platz 302<br />
<em>Eingesetzte Punkte:</em> 240 Punkte<br />
<em>Anzahl freier Plätze für Platzumtausch:</em> 179<br />
<a href="?mod=100&res_id=1">Reservierung stornieren</a>
<a href="?mod=200&res_id=1">Platz umtauschen</a>
</td></tr></tbody></table></main>"""


class FakeSession:
    def __init__(self, html):
        self.html, self.urls = html, []

    def get(self, url):
        self.urls.append(url)
        return type("Resp", (), {"text": self.html})()


def main():
    s = FakeSession(SLOTS)
    assert find_timeslot(s, 1, "12.10.2026", "8:00-12:00") == "?mod=020&locslot_id=2"
    assert s.urls[0].endswith("?mod=010&cat_id=1&date=2026-10-12")
    assert find_timeslot(s, 1, "12.10.2026", "08:00-12:00", preferred_section="westfalica") \
        == "?mod=020&locslot_id=4"
    find_timeslot(s, 1, "12.10.2026", "08:00-12:00", category=4)
    assert "cat_id=4" in s.urls[-1]
    try:
        find_timeslot(s, 45, "12.10.2026", "08:00-12:00")
        raise AssertionError("booked-out library must not borrow the next library's slot")
    except BookingError as e:
        assert "Ausgebucht" in str(e), e

    try:
        find_timeslot(FakeSession('<main><p><span>Fehler:</span> Das Datum 2026-10-13 ist unzulässig.</p></main>'),
                      1, "13.10.2026", "08:00-12:00")
        raise AssertionError("a date that is not open must raise NotOpenYet")
    except NotOpenYet:
        pass

    # week of Mon 05.10.2026, now Wed 07.10. 10:00: Thu..Sat already opened, Sun 11.10 opens Thu 08.10.
    job = lambda **kw: SimpleNamespace(**{"time_slot": "08:00-12:00", "recurring": True,  # noqa: E731
                                          "cron_days": "mon,tue,wed,thu,fri,sat,sun", "target_date": None, **kw})
    now, monday = datetime(2026, 10, 7, 10, 0, tzinfo=TZ), date(2026, 10, 5)
    assert planned_points([job()], monday, now) == 240  # only Sunday, 4 h
    assert planned_points([job(cron_days="mon")], date(2026, 10, 12), now) == 240
    assert planned_points([job(recurring=False, target_date=date(2026, 10, 11), cron_days=None)], monday, now) == 240
    assert planned_points([job(cron_days="")], date(2026, 10, 12), now) == 5 * 240  # empty: Mon-Fri, like the timers

    s = FakeSession(SEATS)
    assert select_seat(s, "x", preferred=[3, 18])[1] == "Kabine G3"
    # no preferred seat free (or none given): a random free one
    assert {select_seat(s, "x", preferred=p)[1] for p in [[7], []] * 20} == {"Kabine G2", "Kabine G3", "Platz 018"}
    details, seats = parse_seats(SEATS)
    assert seats[0][1:] == ("Kabine G2", "max. 8 Personen") and seats[1][2] == "", seats
    assert details["Bibliothek / Platzgruppe"] == ["Zentralbibliothek / Arbeitskabinen", "Adresse: Krummer Timpen 3"]
    assert details["Zeitslot"] == ["08:00–12:00 Uhr"] and "Verfügbare Plätze" not in details
    assert details["links"] == [("Sitzplan", "https://www.ulb.uni-muenster.de/sitzplan")]

    days = parse_overview(OVERVIEW)
    assert [c["cat"] for c in days[0]["categories"]] == [1, 4]
    assert days[0]["categories"][0] == {"name": "Einzelarbeitsplätze", "cat": 1, "date": "2026-10-09",
                                        "load": "geringe Auslastung (0.2 %)", "last_minute": True}
    assert not days[0]["categories"][1]["last_minute"]
    assert days[1] == {"title": "Dienstag, 13.10.2026", "categories": [],
                       "notice": "Reservierung möglich ab Samstag, 10.10.2026, 09:00"}

    account = parse_account(ACCOUNT)
    assert account["weeks"] == [["Mo, 05.10.2026 – So, 11.10.2026", "5", "1.800"]]
    assert [sec["title"] for sec in account["sections"]] == ["Aktuelle Reservierung", "Zukünftige Reservierungen"]
    assert account["sections"][1]["reservations"] == [{
        "id": 1, "when": "Fr, 09.10.2026, 21:00–22:00",
        "lines": ["Zentralbibliothek / Hauptlesesaal / Platz 302", "Eingesetzte Punkte: 0 Punkte"],
        "cancel": "?mod=100&res_id=1&onetime_token=t", "swap": "?mod=200&res_id=1&onetime_token=t",
    }]

    details = reserve_seat(FakeSession(BOOKED), "x")
    assert details == ("Mo, 12.10.2026, 08:00–12:00; "
                       "Zentralbibliothek / Hauptlesesaal im 1. OG (Einzelarbeitsplätze) / Platz 302; "
                       "Eingesetzte Punkte: 240 Punkte"), details
    try:
        reserve_seat(FakeSession("<main><p><span>Fehler:</span> Platz vergeben.</p></main>"), "x")
        raise AssertionError("failed booking must raise")
    except BookingError as e:
        assert str(e) == "Fehler: Platz vergeben.", e

    assert find_link(SEATS, "030", seat_id=1) == "?mod=030&locslot_id=9&seat_id=1&onetime_token=t"
    assert find_link(SEATS, "030", seat_id=7) is None
    assert find_link(ACCOUNT, "100", res_id=1) == "?mod=100&res_id=1&onetime_token=t"

    print("ok")


if __name__ == "__main__":
    main()
