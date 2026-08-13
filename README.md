# Discord Bot Refuge

This Discord bot requires the Opus audio codec library and FFmpeg for voice features.

## Entrypoint et architecture runtime

Le bot démarre via [`main.py`](./main.py). Le Dockerfile et le Procfile lancent :

```bash
python main.py
```

`main.py` est l'entrypoint de production : il configure les logs Railway, les intents Discord, les alertes critiques, charge le token puis instancie [`RefugeBot`](./bot.py).

`bot.py` contient la classe `RefugeBot` réellement utilisée en production. Elle possède le lifecycle principal du bot : initialisation du tracing HTTP Discord, démarrage des services partagés, découverte/chargement des cogs, synchronisation des commandes et fermeture ordonnée. Les tests importent et instrumentent cette même classe ; il ne s'agit pas d'une implémentation parallèle réservée aux tests.

## System dependencies

Le build de production est défini par le [`Dockerfile`](./Dockerfile) racine. Il installe notamment `libopus0` et `ffmpeg` dans l'image runtime, ainsi que les autres dépendances système nécessaires au bot.

Pour un lancement local sur Debian/Ubuntu :

```bash
sudo apt install libopus0 ffmpeg
```

Les dépendances Python de [`requirements.txt`](./requirements.txt) incluent `discord.py[voice]` et `imageio-ffmpeg`.

## FFmpeg options

Les profils FFmpeg ne sont pas définis dans `bot.py`.

Les constantes audio sont centralisées dans [`utils/audio.py`](./utils/audio.py) :

- `FFMPEG_BEFORE` / `FFMPEG_OPTIONS` pour les radios live à faible latence ;
- `FFMPEG_VOD_BEFORE` / `FFMPEG_VOD_OPTIONS` pour les morceaux à la demande avec buffering normal et reconnexion réseau.

[`utils/voice.py`](./utils/voice.py) sélectionne ensuite le profil approprié dans `play_stream()` et le transmet à `discord.FFmpegPCMAudio`.

Les paramètres historiques suivants sont donc toujours utilisés pour le profil radio live, mais leur propriétaire est désormais `utils/audio.py` :

- `-fflags nobuffer` : désactive le tampon d'entrée pour réduire la latence ;
- `-probesize 32k` : réduit les données analysées au démarrage du flux ;
- `-filter:a loudnorm` : applique une normalisation du volume.

Toute évolution des options FFmpeg doit être faite dans `utils/audio.py`, puis validée via les tests de profils audio/voice.

## Configuration

[`.env.example`](./.env.example) est la **référence officielle de la configuration par variables d'environnement** du bot. Il documente les valeurs utilisées par le casino, les salons temporaires, le rate limiter, l'API meter, les alertes, les radios, Mistral, le Refuge, yt-dlp et les fonctionnalités conservées mais désactivées.

Les valeurs non sensibles de l'exemple correspondent aux valeurs par défaut du code. Les secrets sont volontairement laissés vides. Une valeur numérique vide n'est pas une manière valide de « désactiver » un paramètre : utilisez `0` uniquement lorsque `.env.example` indique explicitement que `0` est accepté.

[`config.py`](./config.py) reste la couche de compatibilité importée par les cogs : elle expose les constantes historiques à partir de l'objet `Settings` et contient encore quelques identifiants statiques propres au serveur. Elle ne doit plus servir de liste de référence pour les variables d'environnement.

Pour récupérer un ID dans Discord, activez le *Mode développeur* puis utilisez **Copier l'identifiant** sur le serveur, salon ou rôle concerné. Les IDs Discord sont des Snowflakes numériques ; conservez-les sous forme d'entiers décimaux dans les variables correspondantes.

### Railway

Railway fournit les variables du service au processus sous forme de variables d'environnement. Railway détecte également les fichiers `.env.example` présents à la racine d'un dépôt GitHub afin de proposer les variables à configurer dans l'onglet **Variables** du service.

Le build de ce dépôt est volontairement piloté par le [`Dockerfile`](./Dockerfile) racine. Railway détecte automatiquement ce fichier et l'utilise pour construire le service. L'ancien `nixpacks.toml` a été retiré afin qu'il ne suggère plus l'existence d'un second chemin de build.

Après l'ajout, la modification ou la suppression d'une variable Railway, appliquez les changements staged via un nouveau déploiement pour qu'ils soient pris en compte par le conteneur.

#### Docker non-root et volume persistant

L'image Docker exécute le bot avec l'utilisateur non privilégié `refuge` (`UID/GID 10001`). Deno, yt-dlp, FFmpeg et Python héritent donc de cet utilisateur pendant le fonctionnement normal du bot.

Railway monte toutefois les volumes persistants avec `root` comme propriétaire. Pour un service utilisant le volume `/app/data`, ajoutez la variable Railway spéciale :

```text
RAILWAY_RUN_UID=0
```

Cette valeur autorise uniquement l'entrypoint du conteneur à démarrer avec les privilèges nécessaires pour corriger la propriété du volume. `docker-entrypoint.sh` refuse les chemins de volume hors de `/app/data`, applique les permissions à ce volume puis remplace immédiatement le processus root par `refuge` via `gosu` avant le lancement de `python main.py`.

Le volume Railway doit donc rester monté sur `/app/data`. `RAILWAY_VOLUME_MOUNT_PATH` est fourni automatiquement par Railway et ne doit pas être créé manuellement. `RAILWAY_RUN_UID` est une variable d'infrastructure Railway, pas une variable applicative du bot ; elle n'est donc pas ajoutée à `.env.example`.

### Alertes critiques et observabilité

`CRITICAL_LOG_CHANNEL_ID` fournit uniquement une **notification secondaire** dans Discord. Le handler essaie d'abord le cache `get_channel()`, puis `fetch_channel()` si le salon n'est pas présent en cache. Les tâches d'envoi sont enregistrées dans le registre commun de tâches d'arrière-plan : une erreur de récupération ou d'envoi est donc récupérée et journalisée au lieu d'être absorbée silencieusement.

Discord ne doit pas être considéré comme la source d'alerte principale : si le processus Python tombe, si Discord est indisponible ou si l'event loop ne tourne plus, le bot peut être incapable d'envoyer sa propre alerte.

En production Railway, la stratégie recommandée est :

- conserver les logs structurés stdout/stderr comme source de diagnostic principale ;
- utiliser une **Restart Policy** adaptée au service pour les crashes process ;
- s'appuyer sur les statuts de déploiement et notifications Railway lorsqu'un déploiement devient `Crashed` ;
- configurer des monitors Railway pour CPU, RAM, disque et egress lorsque des seuils opérationnels sont utiles ;
- utiliser si nécessaire les Webhooks Railway vers un système externe pour les changements d'état de déploiement et les alertes ;
- ajouter éventuellement un outil applicatif externe comme Sentry/APM pour la collecte d'exceptions et le suivi applicatif fin.

Le healthcheck Railway est utile au moment du déploiement pour valider que la nouvelle instance devient prête, mais il ne constitue pas un monitoring continu après activation du déploiement.

### Principales familles de variables

La liste exhaustive et les valeurs de référence restent dans [`.env.example`](./.env.example). Elle est organisée par familles :

- Discord, processus et persistance ;
- salons/rôles configurables et alertes ;
- salons vocaux temporaires / Streamer ;
- casino et roulette XP ;
- radios et renommages ;
- Double XP vocal ;
- rate limiter et API meter ;
- Maître du jeu / Mistral ;
- monde du Refuge (panneau, journal, objectifs, construction, Feu, Hall, Casino) ;
- YouTube / yt-dlp ;
- NHL, actuellement désactivé mais conservé dans le dépôt.

## Gestion des secrets

Ne stockez jamais de jetons, clés API, cookies ou autres informations sensibles dans le dépôt. Les entrées sensibles de `.env.example` (`DISCORD_TOKEN`, `MISTRAL_API_KEY`, `YOUTUBE_COOKIES_B64`, `NHL_ODDS_API_KEY`) doivent rester vides dans Git.

En production Railway, renseignez leurs vraies valeurs uniquement dans l'onglet **Variables** du service. Pour un développement local, exportez les variables dans votre environnement avant de lancer `python main.py`.

## Données persistantes

Certaines fonctionnalités (XP, machine à sous, salons temporaires…) écrivent des
fichiers JSON pour conserver leur état. Par défaut, ces fichiers sont stockés
dans le dossier `/app/data` si présent (montage Railway), sinon dans `/data`.
Vous pouvez modifier cet emplacement en définissant la variable
d'environnement `DATA_DIR` :

```bash
export DATA_DIR=/chemin/vers/mes/données
```

`GAMES_DATA_DIR` utilise `${DATA_DIR}/games` par défaut dans le code ; si vous le définissez explicitement, gardez-le cohérent avec le volume persistant choisi.

Assurez-vous que le dossier persistant existe et est accessible en lecture/écriture par le bot. Pour migrer un ancien déploiement utilisant `/data`, copiez vos fichiers vers `/app/data` ou définissez `DATA_DIR=/data`.

Les salons vocaux temporaires sont listés dans `data/temp_vc_ids.json`. Ce fichier doit être conservé entre les redéploiements (volume monté ou dossier `DATA_DIR` persistant), sans quoi les salons existants seront supprimés lors du démarrage.

## Renommage des salons

Les paramètres exposés par variables d'environnement sont documentés dans `.env.example`, notamment :

- `CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL` ;
- `CHANNEL_RENAME_MIN_INTERVAL_GLOBAL` ;
- `CHANNEL_RENAME_DEBOUNCE_SECONDS` ;
- `CHANNEL_RENAME_MAX_RETRIES` ;
- `CHANNEL_RENAME_BACKOFF_BASE`.

Avec les valeurs actuelles, le gestionnaire impose notamment 5 s entre deux renommages d'un même salon, 2 s au niveau global et un debounce de 2 s. Réduire les intervalles augmente la pression sur l'API Discord.

## Sauvegarde des sessions vocales

Les heures d'entrée des membres en vocal sont stockées dans `data/voice_times.json`. Chaque événement vocal planifie une sauvegarde différée de **300 secondes (5 minutes) par défaut**, qui écrit le fichier de manière atomique dans un thread séparé afin de ne pas bloquer l'event loop. Une sauvegarde périodique toutes les 10 minutes est conservée en secours.

Le délai se configure via `VOICE_CP_DEBOUNCE_SECONDS`. `.env.example` utilise lui aussi `300`, afin qu'une copie de la configuration de référence ne transforme pas involontairement le checkpoint de 5 minutes en sauvegarde quasi immédiate.
