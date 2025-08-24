# Discord Bot Refuge

This Discord bot requires the Opus audio codec library and FFmpeg for voice features.

## Entrypoint

The bot launches via [`main.py`](./main.py). Docker images and Procfile run:

```bash
python main.py
```

## System dependencies

Install `libopus0` and `ffmpeg` on Debian/Ubuntu:

```bash
sudo apt install libopus0 ffmpeg
```

The `nixpacks.toml` build configuration already lists `libopus0` to ensure the library is present in production environments.  The Python dependencies in [`requirements.txt`](./requirements.txt) include `discord.py[voice]` and `imageio-ffmpeg` so FFmpeg support is available.

## FFmpeg options

Audio playback relies on FFmpeg. Some useful parameters can be tuned in
`bot.py`:

- `-fflags nobuffer` : désactive le tampon d'entrée pour réduire la latence.
- `-probesize 32k` : diminue les données analysées afin d'accélérer le démarrage du flux.
- `-filter:a loudnorm` : applique une normalisation du volume.

Ces valeurs peuvent être ajustées dans la fonction `_before_opts()` et dans
la variable `audio_opts` selon vos besoins.

## Configuration du serveur

Les IDs propres à votre serveur Discord (rôles, salons, catégories…) sont
réunis dans le fichier [`config.py`](./config.py).
Modifiez les valeurs de ce fichier pour correspondre à votre serveur.

Pour récupérer un ID dans Discord, activez le *Mode développeur* puis
faites un clic droit sur un élément et choisissez **Copier l'identifiant**.

Exemple de personnalisation :

```python
# config.py
ROLE_PC = 123456789012345678  # ID du rôle PC de votre serveur
```

`bot.py` et `view.py` importent ces constantes afin d'éviter toute valeur
en dur dans le code.

## Variables d'environnement

Un fichier d'exemple [`.env.example`](./.env.example) regroupe les variables
essentielles pour exécuter le bot :

- `DISCORD_TOKEN` : jeton du bot fourni par le [Portail développeur Discord](https://discord.com/developers/applications).
- `GUILD_ID` : identifiant du serveur Discord principal. Utilisé pour synchroniser immédiatement les commandes slash sur ce serveur lors du démarrage du bot.
- `OWNER_ID` : identifiant du propriétaire du bot.
- `TZ` : fuseau horaire du processus (défaut : `Europe/Paris`).
- `DATA_DIR` : répertoire de stockage persistant pour les données.
- `CHANNEL_EDIT_MIN_INTERVAL_SECONDS` : intervalle minimal entre deux éditions du même salon.
- `CHANNEL_EDIT_DEBOUNCE_SECONDS` : délai d'agrégation avant l'édition d'un salon.
- `VOICE_CP_DEBOUNCE_SECONDS` : délai avant la sauvegarde des sessions vocales.

Adaptez ces valeurs selon votre déploiement avant de lancer le bot.

## Gestion des secrets

Ne stockez jamais de jetons, de clés API ou d'autres informations sensibles dans le dépôt. Utilisez des variables d'environnement ou un gestionnaire de secrets dédié. Le fichier `.env` est ignoré par Git ; servez-vous du modèle `.env.example` pour documenter les paramètres requis.

## Données persistantes

Certaines fonctionnalités (XP, roulette, salons temporaires…) écrivent des
fichiers JSON pour conserver leur état. Par défaut, ces fichiers sont stockés
dans le dossier `/app/data` si présent (montage Railway), sinon dans `/data`.
Vous pouvez modifier cet emplacement en définissant la variable
d'environnement `DATA_DIR` :

```bash
export DATA_DIR=/chemin/vers/mes/données
```

Assurez-vous que ce dossier existe et est accessible en lecture/écriture par
le bot. Pour migrer un ancien déploiement utilisant `/data`, copiez vos fichiers
vers `/app/data` ou définissez `DATA_DIR=/data`.

Les salons vocaux temporaires sont listés dans `data/temp_vc_ids.json`. Ce
fichier doit être conservé entre les redéploiements (volume monté ou dossier
`DATA_DIR` persistant), sans quoi les salons existants seront supprimés lors du
démarrage.

Le délai avant renommage de ces salons peut être ajusté via la constante
`RENAME_DELAY` dans [`config.py`](./config.py).

Le renommage effectif intervient après un délai total d'environ
`RENAME_DELAY` + `CHANNEL_RENAME_DEBOUNCE_SECONDS`. Avec les valeurs par
défaut (3 s et 2 s), le bot attend environ 5 s avant d'émettre la
requête de renommage, tout en respectant les limites de l'API Discord
(5 s entre deux renommages d'un même salon et 2 s globalement).
Réduire ces valeurs diminue la latence mais augmente la consommation de
ces limites.

La fréquence de vérification des noms de ces salons est définie par la constante
`TEMP_VC_CHECK_INTERVAL_SECONDS` (30 secondes par défaut).

La vérification de l'état de la roulette est contrôlée par la constante
`ROULETTE_BOUNDARY_CHECK_INTERVAL_MINUTES` (5 minutes par défaut).

### Sauvegarde des sessions vocales

Les heures d'entrée des membres en vocal sont stockées dans
`data/voice_times.json`. Chaque événement vocal planifie une sauvegarde
différée (5 min par défaut) qui écrit ce fichier de manière atomique dans un
thread séparé afin de ne pas bloquer l'event loop. Une sauvegarde
périodique toutes les 10 minutes est conservée en secours.

Le délai peut être ajusté via la variable d'environnement
`VOICE_CP_DEBOUNCE_SECONDS`.

## Limitation des éditions de salon

Pour éviter les erreurs HTTP 429 causées par des modifications trop fréquentes
des salons, deux variables d'environnement permettent d'ajuster le rythme :

- `CHANNEL_EDIT_MIN_INTERVAL_SECONDS` (défaut `180`) : intervalle minimal entre
  deux éditions du même salon.
- `CHANNEL_EDIT_DEBOUNCE_SECONDS` (défaut `15`) : délai d'agrégation avant
  d'appliquer les changements.
- `CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS` (défaut `10`) : intervalle minimal
  global entre deux éditions de salons.

Ces variables peuvent être définies dans votre fichier `.env` (voir
`.env.example`).
