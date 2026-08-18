# Migration SQLite — XP et checkpoints vocaux

La première phase de migration remplace la persistance JSON de deux états critiques par une base SQLite unique :

- XP : ancien `DATA_DIR/data.json` → table `xp` ;
- connexions vocales actives : ancien `DATA_DIR/voice_times.json` → table `voice_times`.

La base est créée dans `DATA_DIR/refuge.db`. Le code ne suppose donc pas que Railway utilise `/app/data` : le chemin réel suit la configuration `DATA_DIR` du service.

## Pourquoi `sqlite3` plutôt que `aiosqlite`

Le bot possède déjà un état runtime en mémoire et regroupe ses écritures. Cette migration utilise donc le module `sqlite3` inclus dans Python et déplace toutes les opérations bloquantes avec `asyncio.to_thread()`.

Cela évite d'ajouter une dépendance de production tout en conservant l'event loop Discord non bloquante. Si le futur modèle devient majoritairement SQL avec beaucoup de requêtes concurrentes, `aiosqlite` pourra être réévalué.

## Démarrage automatique

`RefugeBot.setup_hook()` initialise SQLite avant le chargement des cogs. `XPStore.start()` importe automatiquement l'ancien fichier XP si nécessaire. Le cog XP importe de la même manière l'ancien checkpoint vocal.

Chaque migration legacy possède un marqueur dans `app_metadata`, ce qui la rend idempotente. Une fois importés, les JSON historiques ne sont plus la source de vérité runtime et ne sont plus réécrits.

## Migration manuelle et contrôle d'intégrité

Depuis la racine du dépôt :

```bash
python scripts/migrate_to_sqlite.py
```

Le script affiche :

- le chemin réel de `refuge.db` ;
- le nombre d'entrées XP importées ;
- le nombre de checkpoints vocaux importés ;
- le résultat de `PRAGMA quick_check`.

Un résultat attendu est :

```text
PRAGMA quick_check: ok
```

Le script est idempotent : le relancer ne duplique pas les données.

## Railway — configuration obligatoire

SQLite n'est durable sur Railway que si `DATA_DIR` se trouve sur un **Volume** persistant.

Dans Railway :

1. Ouvrir le service du bot.
2. Ouvrir **Volumes**.
3. Vérifier qu'un volume est attaché au service.
4. Vérifier que son **Mount Path** correspond exactement à `DATA_DIR`.
   - si `DATA_DIR=/data`, monter le volume sur `/data` ;
   - si `DATA_DIR=/app/data`, monter le volume sur `/app/data`.
5. Ne pas activer plusieurs replicas avec SQLite sur ce volume.
6. Après déploiement, vérifier les logs de démarrage pour `SQLite persistence ready` puis l'absence d'erreur SQLite.

Le conteneur du dépôt possède déjà le bootstrap nécessaire pour rendre le volume accessible à l'utilisateur non-root `refuge` lorsque Railway monte `/app/data`.

## Sauvegarde / rollback

Pendant cette première phase, ne supprimez pas immédiatement les anciens fichiers JSON du volume. Ils servent de filet de rollback et de preuve de migration, mais ils ne sont plus mis à jour après le basculement.

Avant le premier déploiement SQLite :

1. créer un backup/snapshot du volume Railway ;
2. conserver `data.json`, `data.json.bak`, `voice_times.json` et `voice_times.json.bak` ;
3. déployer ;
4. contrôler `PRAGMA quick_check` et les valeurs XP depuis Discord ;
5. seulement après validation fonctionnelle, considérer les JSON comme archives legacy.

## Limites de cette phase

`daily_stats.json`, `xp_boosts.json` et les autres stores JSON ne sont pas migrés ici. Cette séparation réduit le blast radius et permet de valider XP + voix avant d'étendre SQLite au reste du bot.

Le schéma XP conserve volontairement la clé actuelle `user_id`. Il n'ajoute pas encore de clé `(guild_id, user_id)`, car le bot actuel fonctionne avec un état XP global au serveur cible et cette modification serait une évolution métier distincte.
