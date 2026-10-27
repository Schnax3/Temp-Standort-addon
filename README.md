# Temp-Standort-Add-on

[![Home Assistant Add-on Repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FSchnax3%2FTemp-Standort-addon)

Ein benutzerdefiniertes Home-Assistant-Add-on für temporäre Standort-Alarme. Es überwacht Zustandsänderungen von Personen oder Tracker-Entitäten und sendet bei definierten Ereignissen (Betreten oder Verlassen einer Zone) eine Benachrichtigung per `notify`-Dienst. Jede Regel läuft nach einer vorgegebenen Anzahl an Auslösungen automatisch aus und kann anschließend im Hintergrund gelöscht werden.

## Überblick

Das Add-on eignet sich für Szenarien wie:

- Erinnerung an eine Person, die eine Zone betritt oder verlässt
- einmalige oder wiederholte Benachrichtigungen bei Bewegung im Haus
- temporäre Regeln, die nach einer Anzahl von Auslösungen automatisch verschwinden
- Benachrichtigungen ohne klassische HA-Automationen, die später manuell gelöscht werden müssen

Es arbeitet direkt mit Home Assistant Core über die Supervisor-API und die WebSocket-Schnittstelle. Die Regeln werden in `/data/rules.json` gespeichert und verwaltet.

## Funktionen

- Verfolgt `person.*` und `device_tracker.*` Entitäten
- Überwacht `zone.*`-Zustände
- Unterstützt `enter` und `leave` Ereignisse
- Sendet Meldungen per `notify.mobile_app_*` oder anderem gültigen `notify`-Dienst
- Konfigurierbare Wiederholungsanzahl pro Regel
- Automatische Bereinigung nach Ablauf
- einfache Weboberfläche im Add-on-Panel
- keine YAML-Automationen nötig

## Verwendete Architektur

- Home Assistant Supervisor
- Python 3
- aiohttp
- WebSocket-Events von Home Assistant

## Installation

1. Öffne deine Home-Assistant-Instanz.
2. Gehe zu `Einstellungen` → `Add-ons` → `Add-on-Shop`.
3. Klicke rechts oben auf die drei Punkte und wähle `Repository hinzufügen`.
4. Füge folgende URL ein:

   `https://github.com/Schnax3/Temp-Standort-addon`

5. Aktualisiere den Add-on-Shop und installiere `Standort-Alarme (temporär)`.
6. Starte das Add-on und prüfe die Konfiguration.

## Nutzung

Das Add-on enthält eine kleine Weboberfläche, über die Regeln verwaltet werden können. Pro Regel werden folgende Werte definiert:

- betroffene Entity (z. B. `person.musterperson`)
- Zielzone (z. B. `zone.home`)
- Ereignis: `enter` oder `leave`
- Notify-Dienst, z. B. `notify.mobile_app_iphone`
- Anzahl der Auslösungen
- Meldungstext
- optionaler Name der Regel

Beispiel:

- `person.max` betritt `zone.home` → Nachricht an `notify.mobile_app_iphone`
- 2 Auslösungen → Benachrichtigung wird nach dem zweiten Trigger beendet

## Beispiel für eine Benachrichtigung

Der Standardtext kann angepasst werden. Ein Beispiel:

`{person} hat {zone} {event}`

Das wird je nach Ereignis in:

- `Max hat Zuhause betreten`
- `Max hat Zuhause verlassen`

umgewandelt.

## Dateistruktur

```text
Temp-Standort-addon/
├── README.md
├── repository.yaml
└── temp_location_notify/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    └── app/
        ├── main.py
        └── static/
```

## Hinweise

- Das Add-on ist für `aarch64` konzipiert, wie in `config.yaml` hinterlegt.
- Regeln werden nach Ablauf automatisch entfernt, verbleibende erledigte Einträge werden nach 24 Stunden bereinigt.
- Für Benachrichtigungen müssen gültige `notify`-Dienste in Home Assistant vorhanden sein.

## Lizenz

Dieses Projekt wird ohne zusätzliche Lizenzangabe im Repository bereitgestellt. Bitte prüfe bei Bedarf die jeweilige Projekt- und Add-on-Konfiguration in deinem eigenen Home-Assistant-System.

## Beitrag

Beiträge, Ideen oder Verbesserungen sind willkommen. Wenn du das Add-on erweitern möchtest, kannst du das Repository forken und einen Pull Request erstellen.

