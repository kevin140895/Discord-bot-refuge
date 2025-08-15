# 1. Choisir une image Python officielle
FROM python:3.11-slim

# 2. Installer les bibliothèques système nécessaires
RUN apt-get update && \
    apt-get install -y libopus0 ffmpeg && \
    apt-get clean

# 3. Créer un dossier pour ton application
WORKDIR /app

# 4. Copier tout le code de ton projet dans l’image
COPY . /app

# 5. Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# 6. Démarrer le bot
CMD ["python", "bot.py"]
