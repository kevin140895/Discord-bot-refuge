# Maître du jeu — fournisseur IA

Le fallback conversationnel V3 utilise Mistral via l'API Chat Completions.

Variables Railway :

- `MISTRAL_API_KEY` : clé Mistral/Vibe Code (requise) ;
- `MISTRAL_MODEL` : modèle optionnel, défaut `mistral-medium-3-5` ;
- `MISTRAL_API_URL` : endpoint optionnel, défaut `https://api.mistral.ai/v1/chat/completions`.

Les anciennes variables `OPENAI_*` ne sont plus utilisées par le fallback IA.
