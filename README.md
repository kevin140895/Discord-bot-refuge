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

- `-buffer_size 64k` : limite le tampon d'entrée pour réduire la latence.
- `-probesize 32k` : diminue les données analysées afin d'accélérer le démarrage du flux.
- `-filter:a loudnorm` : applique une normalisation du volume.

Ces valeurs peuvent être ajustées dans la fonction `_before_opts()` et dans
la variable `audio_opts` selon vos besoins.
