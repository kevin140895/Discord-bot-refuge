# Discord Bot Refuge

This Discord bot requires the Opus audio codec library for voice features.

## System dependencies

Install `libopus0` on Debian/Ubuntu:

```bash
sudo apt install libopus0
```

The `nixpacks.toml` build configuration already lists `libopus0` to ensure the library is present in production environments.

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

## Données persistantes

Certaines fonctionnalités (XP, roulette, salons temporaires…) écrivent des
fichiers JSON pour conserver leur état.  Par défaut, ces fichiers sont
stockés dans le dossier `/data`.  Vous pouvez modifier cet emplacement en
définissant la variable d'environnement `DATA_DIR` :

```bash
export DATA_DIR=/chemin/vers/mes/données
```

Assurez-vous que ce dossier existe et est accessible en lecture/écriture par
le bot.
