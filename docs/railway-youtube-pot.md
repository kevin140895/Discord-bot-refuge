# YouTube PO Token provider sur Railway

Music 2.0 utilise `yt-dlp` pour résoudre les flux YouTube. Lorsque YouTube exige
un Proof-of-Origin token pour les requêtes GVS, le bot peut déléguer la génération
du token à un service HTTP bgutil séparé.

## Architecture

- service principal : bot Discord Python ;
- service privé : `youtube-pot` ;
- image du provider : `brainicism/bgutil-ytdlp-pot-provider:2.0.2` ;
- port du provider : `4416` ;
- communication : réseau privé Railway uniquement ;
- variable du bot :
  `YOUTUBE_POT_PROVIDER_URL=http://youtube-pot.railway.internal:4416`.

Le provider ne doit pas avoir de domaine public.

## Déploiement Railway

1. Dans le même projet et le même environnement que le bot, ajouter un nouveau
   service à partir d'une image Docker.
2. Utiliser l'image `brainicism/bgutil-ytdlp-pot-provider:2.0.2`.
3. Nommer le service `youtube-pot`.
4. Ne pas générer de domaine public pour ce service.
5. Laisser le provider écouter sur son port par défaut `4416`.
6. Dans le service du bot, ajouter :
   `YOUTUBE_POT_PROVIDER_URL=http://youtube-pot.railway.internal:4416`.
7. Redéployer le bot avec la version contenant le plugin
   `bgutil-ytdlp-pot-provider==2.0.2`.

L'image Docker 2.0.2 écoute sur toutes les interfaces **à l'intérieur du conteneur**,
pour être accessible via le réseau privé Railway. Ne pas créer de domaine public,
de proxy TCP public ou de route exposant le port 4416 : ce service ne possède
pas d'authentification HTTP. La liaison en réseau privé utilise le DNS du service
`youtube-pot.railway.internal`. Les installations natives hors Docker lient le
serveur à `localhost` par défaut : ne pas confondre les deux modes.

Le plugin Python et l'image Docker doivent utiliser la même version 2.0.2.
Les empreintes SHA-256 du lock Python sont celles du wheel et de la source
publiés officiellement sur PyPI.

## Validation dans les logs

Au démarrage du bot, vérifier la présence de :

```text
[ytdlp] PO Token provider activé client=mweb transport=http
```

Ensuite lancer une recherche YouTube depuis Music 2.0. Vérifier la présence du
provider en sortie yt-dlp verbose (`bgutil:http-2.0.2`) **et** d'une ligne indiquant
la génération effective d'un PO Token, par exemple
`[youtube] [pot:bgutil:http] Generating a gvs PO Token ...`.
La simple présence du plugin dans les logs de démarrage ne garantit pas qu'un
token soit demandé. Vérifier qu'aucune erreur HTTP 403 ni erreur d'extraction
n'apparaisse pour la recherche et le lien direct.

Attention : un PO Token ne contourne pas tous les contrôles anti-robots YouTube.
En cas de `Sign in to confirm you're not a bot`, contrôler séparément la validité
des cookies, sans jamais copier leur contenu dans les logs ou Discord.

Le provider peut aussi être contrôlé depuis un autre service Railway avec :

```text
GET http://youtube-pot.railway.internal:4416/ping
```

La réponse doit contenir la version et l'uptime du provider.

## Discord

Aucune modification de salon, rôle, permission, intent ou application Discord
n'est nécessaire. Le correctif concerne uniquement la chaîne yt-dlp/YouTube et
l'infrastructure Railway.
