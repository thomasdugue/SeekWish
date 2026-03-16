# Prompt de handoff — Audiophile Wishlist

## Contexte

Tu reprends un projet de plugin Nicotine+ appelé "Audiophile Wishlist". C'est un plugin pour le client Soulseek open-source Nicotine+ (https://github.com/nicotine-plus/nicotine-plus), accompagné d'un companion app web.

Le projet a été développé dans une conversation Claude.ai. Le code fonctionne : Deezer est testé et opérationnel en production (l'utilisateur a importé 100 tracks avec succès). Les providers Spotify et YouTube Music sont codés mais pas encore validés en conditions réelles.

## Ce qui existe

### Plugin N+ (`audiophile_wishlist/`)
- Se charge dans Nicotine+ comme plugin natif
- Deux fonctions principales :
  1. **Import de playlists** : crée des entrées wishlist N+ à partir de tracks extraites par le companion app (via fichier `pending_import.json`)
  2. **Filtre qualité** : intercepte les résultats de recherche wishlist via `events.connect("file-search-response")` et auto-download ou queue en pause les fichiers qui passent les critères (format lossless, bitrate min, sample rate min, bit depth min, taille min)
- Commandes chat : `/aw-import <url>`, `/aw-status`, `/aw-reset-stats`
- 100% stdlib Python (contrainte N+ : pas de packages externes installables)

### Companion app (`companion.py`)
- Serveur HTTP local (localhost:8484) avec UI web embarquée
- L'utilisateur colle une URL de playlist publique → extraction des tracks → affichage → clic "Envoyer à Nicotine+"
- 3 providers :
  - **Deezer** : API REST publique `api.deezer.com/playlist/{id}/tracks` — ✅ fonctionne
  - **Spotify** : token anonyme extrait de la page embed → API officielle — ⚠️ codé, pas testé
  - **YouTube Music** : parsing de `ytInitialData` — ⚠️ codé, pas testé

### Tests
- 26 tests unitaires pytest couvrant : détection URL, filtres qualité, scoring

### Configuration VS Code
- Settings, tasks (install, test, lint), launch config, extensions recommandées
- Tout cross-platform (macOS / Linux / Windows)

## Priorités immédiates (par ordre)

1. **Fixer le provider Spotify** : le code utilise un token anonyme extrait de la page embed Spotify puis appelle l'API officielle. Il faut tester en live et corriger si le pattern d'extraction du token a changé.

2. **Fixer le provider YouTube Music** : même approche — tester le parsing de `ytInitialData` en live, adapter les sélecteurs si Google a changé la structure.

3. **Améliorer l'installation** : actuellement c'est des commandes `cp` manuelles. Il faudrait soit un script `install.sh`/`install.bat`, soit un `make install` plus guidé avec des messages clairs.

4. **Ajouter la sélection de tracks** : dans le companion app, permettre de décocher des tracks avant l'envoi à N+ (checkbox par track dans la liste).

## Contraintes techniques

- Le plugin N+ (`__init__.py`) doit rester en **un seul fichier** Python avec **zéro dépendance externe** (stdlib uniquement : urllib, json, re, os, time, threading)
- Le companion app (`companion.py`) est aussi stdlib uniquement
- Le plugin utilise le système de commandes N+ 3.3+ : dict plat `self.commands = {"nom": {"callback": ..., "description": ...}}`
- Les callbacks de commandes ont la signature : `def callback(self, args, **_unused)`
- Pour afficher du texte dans le chat : `self.output(text)` (pas `self.echo_message`)
- Le plugin s'installe dans `~/.local/share/nicotine/plugins/audiophile_wishlist/` (macOS/Linux) ou `%AppData%\nicotine\plugins\audiophile_wishlist\` (Windows)

## Repo Git

Le code source est dans ce repo. Lis `CLAUDE.md` à la racine pour l'architecture complète, l'API N+ utilisée, et le status détaillé de chaque composant.

## Comment tester

```bash
# Tests unitaires
make test

# Lancer le companion
python3 companion.py
# → ouvre http://localhost:8484
# → colle une URL Deezer pour vérifier que l'extraction fonctionne
# → teste Spotify et YouTube Music

# Installer dans N+
make install
# → dans N+ : Préférences → Plugins → activer "Audiophile Wishlist"
# → /aw-status dans un chat room pour vérifier
```
