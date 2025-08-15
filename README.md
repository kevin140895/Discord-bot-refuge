# Discord Bot Refuge

This Discord bot requires the Opus audio codec library for voice features.

## System dependencies

Install `libopus0` on Debian/Ubuntu:

```bash
sudo apt install libopus0
```

The `nixpacks.toml` build configuration already lists `libopus0` to ensure the library is present in production environments.
