# Utilise une image Python légère
FROM python:3.11-slim

# Installation des dépendances système nécessaires pour ffmpeg, opus, Pillow, etc.
RUN apt-get update && \
    apt-get install -y \
    libopus0 \
    ffmpeg \
    libjpeg-dev \
    zlib1g-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Répertoire de travail dans le conteneur
WORKDIR /app

# Copie tous les fichiers du projet
COPY . .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Lancement du bot
CMD ["python", "bot.py"]
