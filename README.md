# Bot SeLoger

Bot Python qui automatise la recherche d'annonces sur [SeLoger](https://www.seloger.com) et l'envoi de messages de contact.

## Fonctionnement

1. **Recherche** — résout la ville et construit l’URL SeLoger  
2. **Collecte** — récupère les liens d’annonces (Selenium)  
3. **Extraction** — parse les fiches en parallèle (`requests` + BeautifulSoup)  
4. **Contact** — remplit et envoie le formulaire de contact (optionnel)

Les annonces contactées sont suivies dans un fichier Excel (`data/annonces_contactees.xlsx`) pour éviter les doublons.

## Prérequis

- Python 3.10+
- Google Chrome

## Installation

```bash
git clone https://github.com/VOTRE_USER/bot_seloger1.git
cd bot_seloger1
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Modifiez vos informations et critères dans `main.py` (bloc `if __name__ == "__main__"`) ou dans `config.py` :

| Paramètre | Description |
|-----------|-------------|
| `ville` | Ville ciblée |
| `transaction` | `"achat"` ou `"location"` |
| `type_logement` | `"appartement"`, `"maison"`, `"apartoumaison"`, etc. |
| `budget_max` | Budget max (€) |
| `surface_min` / `pieces_min` / `chambres_min` | Filtres |
| `prenom` / `nom` / `email` / `telephone` | Coordonnées du formulaire |
| `envoyer_message` | `False` = simulation, `True` = envoi réel |
| `nombre_messages` | Limite d’envois (`0` = illimité) |

Le message type se configure via `MESSAGE_CONTACT` dans `config.py`.

## Lancement

```bash
python main.py
```

En mode simulation (`envoyer_message=False`), le bot scrape et enrichit les annonces sans envoyer de messages.

## Structure

```
├── main.py              # Point d'entrée
├── config.py            # Paramètres globaux
├── url_builder.py       # Construction des URLs de recherche
├── city_resolver.py     # Résolution du code ville SeLoger
├── scraper.py           # Collecte des liens (Selenium)
├── fetcher.py           # Extraction parallèle des fiches
├── contact_manager.py   # Remplissage du formulaire
├── excel_manager.py     # Suivi Excel anti-doublon
├── logger.py            # Logs console + fichier
├── utils.py             # Utilitaires
└── requirements.txt
```

Dossiers créés automatiquement à l’exécution (ignorés par git) :

- `data/` — Excel + cache villes  
- `logs/` — journaux  
- `debug/` / `screenshots/` — dumps en cas d’erreur  

## Avertissement

L’automatisation d’un site web peut être contraire à ses conditions d’utilisation. Utilisez ce projet à vos risques, de façon raisonnable, et privilégiez le mode simulation pour tester.
