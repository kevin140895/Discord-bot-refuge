# YouTube PO Token provider sur Railway

Music 2.0 utilise `yt-dlp` pour résoudre les flux YouTube. Lorsque YouTube exige
un Proof-of-Origin token pour les requêtes GVS, le bot peut déléguer la génération
du token à un service HTTP bgutil séparé.

## Architecture

- service principal : bot Discord Python ;
- service privé : `youtube-pot` ;
- image du provider : `brainicism/bgutil-ytdlp-pot-provider:1.3.1` ;
- port du provider : `4416` ;
- communication : réseau privé Railway uniquement ;
- variable du bot :
  `YOUTUBE_POT_PROVIDER_URL=http://youtube-pot.railway.internal:4416`.

Le provider ne doit pas avoir de domaine public.

## Déploiement Railway

1. Dans le même projet et le même environnement que le bot, ajouter un nouveau
   service à partir d'une image Docker.
2. Utiliser l'image `brainicism/bgutil-ytdlp-pot-provider:1.3.1`.
3. Nommer le service `youtube-pot`.
4. Ne pas générer de domaine public pour ce service.
5. Laisser le provider écouter sur son port par défaut `4416`.
6. Dans le service du bot, ajouter :
   `YOUTUBE_POT_PROVIDER_URL=http://youtube-pot.railway.internal:4416`.
7. Redéployer le bot avec la version contenant le plugin
   `bgutil-ytdlp-pot-provider==1.3.1`.

Le serveur bgutil 1.3.1 écoute sur IPv6 (`[::]`) avec fallback IPv4, ce qui est
compatible avec le réseau privé Railway, y compris les anciens environnements
qui ne résolvent les domaines privés qu'en IPv6.

## Validation dans les logs

Au démarrage du bot, vérifier la présence de :

```text
[ytdlp] PO Token provider activé client=mweb transport=http
```

Ensuite lancer une recherche YouTube depuis Music 2.0 et vérifier qu'aucune ligne
`HTTP error 403 Forbidden` n'apparaît lorsque FFmpeg ouvre le flux.

Le provider peut aussi être contrôlé depuis un autre service Railway avec :

```text
GET http://youtube-pot.railway.internal:4416/ping
```

La réponse doit contenir la version et l'uptime du provider.

## Discord

Aucune modification de salon, rôle, permission, intent ou application Discord
n'est nécessaire. Le correctif concerne uniquement la chaîne yt-dlp/YouTube et
l'infrastructure Railway.
