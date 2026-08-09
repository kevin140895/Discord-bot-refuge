# Utilise une image Python légère
FROM python:3.11-slim

# Installation des dépendances système nécessaires pour ffmpeg, opus, Pillow, etc.
RUN apt-get update && \
    apt-get install -y \
    libopus0 \
    ffmpeg \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    ca-certificates \
    unzip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# yt-dlp requiert désormais un runtime JavaScript externe pour une prise en
# charge complète de YouTube. Deno est le runtime recommandé et activé par
# défaut par yt-dlp.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    deno --version

# Répertoire de travail dans le conteneur
WORKDIR /app

# Copie tous les fichiers du projet
COPY . .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Lancement du bot
CMD ["python", "main.py"]
