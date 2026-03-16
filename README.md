# SeekWish — Plugin Nicotine+

Plugin Nicotine+ pour les audiophiles : filtre qualité avancé sur la wishlist + import de playlists publiques.

## Installation

### Option A — Depuis VS Code (recommandé)
1. Ouvre le projet dans VS Code
2. **Cmd+Shift+B** (Mac) ou **Ctrl+Shift+B** (Windows) → sélectionne "Install plugin"
3. Dans Nicotine+ → Préférences → Plugins → Active "SeekWish"

### Option B — Depuis le Terminal
```bash
# Mac / Linux
cp -r audiophile_wishlist ~/.local/share/nicotine/plugins/

# Windows (PowerShell)
Copy-Item -Recurse audiophile_wishlist "$env:APPDATA\nicotine\plugins\"
```

### Option C — Via Make
```bash
make install    # auto-détecte Mac/Linux/Windows
```

Puis dans Nicotine+ → Préférences → Plugins → Active "SeekWish"

## Fonctionnalités

### Mode 1 — Filtre qualité (automatique)
Le plugin intercepte les résultats de recherche de la wishlist et filtre par :
- **Format** : FLAC, WAV, ALAC, APE, WV, AIFF (configurable)
- **Bitrate minimum** : 800 kbps par défaut (lossless)
- **Sample rate minimum** : 44100 Hz (CD quality)
- **Bit depth minimum** : 16 bit
- **Taille minimum** : 5 MB (filtre les mauvais rips)

Deux modes de téléchargement :
- **semi** (défaut) : met en queue en pause — tu valides manuellement
- **auto** : télécharge directement si les critères matchent

### Mode 2 — Import de playlists
Importe les tracks d'une playlist publique directement dans la wishlist Nicotine+.

```
/aw-import https://www.deezer.com/playlist/908622995
/aw-import https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
/aw-import https://music.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf
```

## Commandes

| Commande | Description |
|----------|-------------|
| `/aw-import <url>` | Importer une playlist publique |
| `/aw-status` | Voir les réglages et statistiques |
| `/aw-reset-stats` | Réinitialiser les statistiques |

## Fiabilité des providers

| Provider | Méthode | Fiabilité |
|----------|---------|-----------|
| Deezer | API REST publique (JSON) | ⭐⭐⭐ Stable |
| Spotify | Parsing de la page embed | ⭐⭐ Peut casser si Spotify change son HTML |
| YouTube Music | Parsing de ytInitialData | ⭐ Fragile, Google change souvent la structure |

## Notes techniques

- Zéro dépendance externe (stdlib Python uniquement : urllib, json, re)
- Le filtre qualité utilise `events.connect("file-search-response")` — API non-standard mais fonctionnelle
- Compatible Nicotine+ 3.3.x
- Le plugin ne crashe jamais N+ : chaque provider échoue indépendamment avec un message d'erreur
