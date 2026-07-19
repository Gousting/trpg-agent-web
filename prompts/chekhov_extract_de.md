Zusätzlich führst du die **Chekhov-Liste** der Spielleitung: unaufgelöste Details, die sich
später als Callback zurückspielen lassen. Deine JSON-Antwort enthält dafür ein weiteres Feld
`chekhov` in genau dieser Form:

{"chekhov": {"new": [{"detail": "...", "weight": 2}], "resolved": ["t3"]}}

Regeln für `chekhov`:

- `new`: höchstens 5 **unaufgelöste** Details aus dieser Sitzung — erwähnte Objekte,
  Andeutungen, offene Versprechen, unbeantwortete Fragen. Je ein kurzer deutscher Satz
  (`detail`, max. 200 Zeichen) mit `weight` 1–3 (3 = starkes Callback-Material: geheimnisvoll,
  emotional aufgeladen, mehrfach erwähnt).
- KEINE aktiven Quests oder Aufträge — die stehen bereits woanders. Keine Details, die einem
  der nummerierten offenen Fäden ähneln.
- `resolved`: die IDs (z. B. `"t3"`) der **bisherigen offenen Fäden**, die in dieser Sitzung
  erkennbar aufgelöst wurden (die Frage beantwortet, das Objekt erklärt, das Versprechen
  eingelöst). Nur bei klarer Auflösung — im Zweifel offen lassen.
- Durchsuche dafür den gesamten Sitzungsverlauf, auch den Abschnitt „Früherer Verlauf dieser
  Sitzung" — aber notiere aus diesem Abschnitt KEINE NSC-Erinnerungen (die sind dort schon
  ausgewertet).
- Nichts gefunden → leere Listen.
