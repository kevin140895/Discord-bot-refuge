# Migration SQLite — état XP critique

La migration SQLite couvre désormais quatre états du système XP dans une base unique :

- XP : ancien `DATA_DIR/data.json` → table `xp` ;
- connexions vocales actives : ancien `DATA_DIR/voice_times.json` → table `voice_times` ;
- statistiques quotidiennes : ancien `DATA_DIR/daily_stats.json` → table `daily_stats` ;
- Double XP personnel : ancien `DATA_DIR/xp_boosts.json` → tables `xp_boosts` et `xp_boost_history`.

La base est créée dans `DATA_DIR/refuge.db`. Le code ne suppose donc pas que Railway utilise `/app/data` : le chemin réel suit la configuration `DATA_DIR` du service.

## Pourquoi `sqlite3` plutôt que `aiosqlite`

Le bot possède déjà un état runtime en mémoire et regroupe ses écritures. Cette migration utilise donc le module `sqlite3` inclus dans Python et déplace toutes les opérations bloquantes avec `asyncio.to_thread()`.

Cela évite d'ajouter une dépendance de production tout en conservant l'event loop Discord non bloquante. Si le futur modèle devient majoritairement SQL avec beaucoup de requêtes concurrentes, `aiosqlite` pourra être réévalué.

Toutes les écritures SQLite passent par le même verrou asynchrone du processus. Les remplacements de snapshots utilisent une transaction explicite afin qu'un lecteur ne voie jamais un état partiellement remplacé.

## Démarrage automatique

`RefugeBot.setup_hook()` initialise SQLite avant le chargement des cogs. `XPStore.start()` importe automatiquement l'ancien fichier XP si nécessaire. Le cog XP importe de la même manière les anciens checkpoints vocaux, statistiques quotidiennes et boosts personnels.

Chaque migration legacy possède un marqueur dans `app_metadata`, ce qui la rend idempotente. Une fois importés, les JSON historiques ne sont plus la source de vérité runtime et ne sont plus réécrits.

### Compatibilité des boosts legacy

Deux formats historiques sont acceptés pour `xp_boosts.json` :

- ancien format : `user_id -> date d'expiration` ;
- format récent : `started_at`, `expires_at` et historique borné.

Pour un ancien boost encore actif dont seule l'expiration est connue, la migration fixe son début au moment de l'import. Cela évite d'inventer une période Double XP antérieure au basculement SQLite et donc d'accorder rétroactivement du bonus vocal.

## Migration manuelle et contrôle d'intégrité

Depuis la racine du dépôt :

```bash
python scripts/migrate_to_sqlite.py
```

Le script affiche :

- le chemin réel de `refuge.db` ;
- le nombre d'entrées XP importées ;
- le nombre de checkpoints vocaux importés ;
- le nombre de lignes de statistiques quotidiennes importées ;
- le nombre de boosts personnels importés ;
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
5. Ne pas activer plusieurs replicas qui écrivent simultanément dans cette base SQLite.
6. Après déploiement, vérifier les logs de démarrage pour `SQLite persistence ready` puis l'absence d'erreur SQLite.

Railway monte le volume au démarrage du service. La migration ne doit donc pas être déplacée vers une étape de build ou de pre-deploy qui n'aurait pas accès au volume persistant.

Le conteneur du dépôt possède déjà le bootstrap nécessaire pour rendre le volume accessible à l'utilisateur non-root `refuge` lorsque Railway monte `/app/data`.

## Sauvegarde / rollback

Ne supprimez pas immédiatement les anciens fichiers JSON du volume. Ils servent de filet de rollback et de preuve de migration, mais ils ne sont plus mis à jour après le basculement.

Avant le premier déploiement du lot 2A :

1. créer un backup/snapshot du volume Railway ;
2. conserver `data.json`, `data.json.bak`, `voice_times.json`, `voice_times.json.bak`, `daily_stats.json`, `daily_stats.json.bak`, `xp_boosts.json` et `xp_boosts.json.bak` lorsqu'ils existent ;
3. déployer ;
4. contrôler `PRAGMA quick_check` ;
5. vérifier les valeurs XP et le fonctionnement du Double XP depuis Discord ;
6. redémarrer le service et confirmer que l'état est toujours présent ;
7. seulement après validation fonctionnelle, considérer ces JSON comme archives legacy.

## Modifications Discord

Aucune modification n'est nécessaire dans le Developer Portal, les rôles, les permissions ou les salons pour ce lot. Les commandes et interactions Discord conservent le même contrat ; seul le backend de persistance change.

## Limites après le lot 2A

Les autres stores JSON du bot ne sont pas migrés ici. Cela concerne notamment l'économie, le casino, les salons temporaires, les saisons, les succès, les objectifs communautaires, le Refuge vivant et les événements.

Cette séparation limite le blast radius : XP, voix, statistiques quotidiennes et boosts personnels peuvent être validés ensemble avant la migration des autres domaines métier.

Le schéma XP conserve volontairement la clé actuelle `user_id`. Il n'ajoute pas encore de clé `(guild_id, user_id)`, car le bot actuel fonctionne avec un état XP global au serveur cible et cette modification serait une évolution métier distincte.
