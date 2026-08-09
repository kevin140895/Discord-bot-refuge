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
    git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# yt-dlp requiert désormais un runtime JavaScript externe pour une prise en
# charge complète de YouTube. Deno est le runtime recommandé et activé par
# défaut par yt-dlp.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    deno --version

# Installe le générateur de Proof-of-Origin tokens utilisé par le plugin
# bgutil-ytdlp-pot-provider. Le mode script est adapté au volume du bot et
# évite d'exposer un service HTTP supplémentaire. Le checkout doit rester à la
# même version que le paquet Python déclaré dans requirements.txt.
ARG BGUTIL_POT_PROVIDER_VERSION=1.3.1
RUN git clone --depth 1 --single-branch \
        --branch "${BGUTIL_POT_PROVIDER_VERSION}" \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        /root/bgutil-ytdlp-pot-provider && \
    cd /root/bgutil-ytdlp-pot-provider/server && \
    deno install --allow-scripts=npm:canvas --frozen && \
    rm -rf /root/bgutil-ytdlp-pot-provider/.git

# Répertoire de travail dans le conteneur
WORKDIR /app

# Copie tous les fichiers du projet
COPY . .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Lancement du bot
CMD ["python", "main.py"]
