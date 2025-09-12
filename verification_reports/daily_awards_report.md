# Daily Awards Verification Report

## Résultats A–F

| Item | Description | Statut |
|------|-------------|:------:|
| A | Mentions et contenu (@everyone, `<@id>`) | PASS |
| B | Embed & encodage du classement | FAIL |
| C | Planification 00:00 Europe/Paris | FAIL |
| D | Idempotence / verrou asynchrone | PASS |
| E | Salon cible unique | PASS |
| F | Robustesse I/O | PASS |

## Channel IDs détectés

- `config.py:80` – `AWARD_ANNOUNCE_CHANNEL_ID = 1400552164979507263`
- `config.py:93` – `TIKTOK_ANNOUNCE_CH = 1400552164979507263`
- `config.py:94` – `ACTIVITY_SUMMARY_CH = 1400552164979507263`
- `config.py:100` – `REMINDER_CHANNEL_ID: int = 1400552164979507263`
- `config.py:119` – `ANNOUNCE_CHANNEL_ID: int = 1400552164979507263`

Le module Daily Awards utilise `1400552164979507263` pour l'annonce ; aucune autre référence à l'ancien ID `1400550888246083585` n'a été trouvée pour cette fonctionnalité.

## Suggestions de correctifs (non appliqués)

```diff
--- a/cogs/daily_awards.py
+++ b/cogs/daily_awards.py
@@
-        embed.add_field(name="MVP", value=value, inline=False)
+        embed.add_field(name="👑 MVP", value=value, inline=False)
@@
-        embed.add_field(name="Écrivain", value=value, inline=False)
+        embed.add_field(name="📜 Écrivain", value=value, inline=False)
@@
-        embed.add_field(name="Voix", value=value, inline=False)
+        embed.add_field(name="🎶 Voix", value=value, inline=False)
@@
-            target = datetime.combine(now.date(), time(hour=0, tzinfo=PARIS_TZ))
-            if now >= target:
-                target += timedelta(days=1)
+            target = datetime.combine(now.date(), time(hour=0, tzinfo=PARIS_TZ))
+            if now >= target:
+                next_day = now.date() + timedelta(days=1)
+                target = datetime.combine(next_day, time(hour=0, tzinfo=PARIS_TZ))
```

## Tests échoués / remarques

- Transition DST d'octobre non gérée (test marqué xfail).
