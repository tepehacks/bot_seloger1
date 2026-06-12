"""
config.py — Configuration globale du bot SeLoger.

Centralise tous les paramètres modifiables : navigateur, délais, chemins,
informations de contact par défaut.
"""

from pathlib import Path

# ── Répertoires ───────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
DATA_DIR        = BASE_DIR / "data"
LOGS_DIR        = BASE_DIR / "logs"
EXCEL_FILE      = DATA_DIR / "annonces_contactees.xlsx"

# ── Navigateur ────────────────────────────────────────────────────────────────
HEADLESS: bool = False          # Mode sans fenêtre — gain de vitesse significatif
WINDOW_WIDTH:  int = 1920
WINDOW_HEIGHT: int = 1080
PAGE_LOAD_TIMEOUT: int = 30     # secondes
IMPLICIT_WAIT:     int = 5      # réduit de 10 à 5 — les WebDriverWait explicites prennent le relais

# Préférences Chrome : désactive images, notifications, géolocalisation, popups
# → réduit fortement le temps de chargement des pages
CHROME_PREFS: dict = {
    "profile.managed_default_content_settings.images": 2,   # 2 = bloquer
    "profile.default_content_setting_values.notifications": 2,
    "profile.default_content_setting_values.geolocation": 2,
    "profile.default_content_setting_values.popups": 2,
    "profile.default_content_setting_values.media_stream": 2,
}

# ── Délais humains (secondes) — réduits au minimum anti-bot ──────────────────
WAIT_MIN: float = 0.5
WAIT_MAX: float = 1.5

# ── Parallélisme — extraction des fiches détail ───────────────────────────────
MAX_WORKERS: int = 6   # 4 à 8 threads simultanés pour requests+BS4

# ── SeLoger ───────────────────────────────────────────────────────────────────
SELOGER_BASE_URL    = "https://www.seloger.com"
SELOGER_SEARCH_PATH = "/classified-search"

TRANSACTION_MAP: dict[str, str] = {
    "achat":    "Buy",
    "location": "Rent",
}

TYPE_LOGEMENT_MAP: dict[str, str] = {
    "appartement":        "Apartment",
    "maison":             "House",
    "immeuble":           "Building",
    "terrain":            "Plot",
    "parking":            "Parking",
    "appartement,maison": "House,Apartment",
    "maison,appartement": "House,Apartment",
    "apartoumaison":      "House,Apartment",
}

TYPE_LOGEMENT_USE_FOR: dict[str, str] = {
    "terrain": "Mixed,Living",
    "parking": "Mixed,Living",
}

# ── Message de contact ────────────────────────────────────────────────────────
MESSAGE_CONTACT: str = (
    "Bonjour,\n\n"
    "Votre bien a retenu toute mon attention.\n\n"
    "Je souhaiterais savoir s'il est toujours disponible et s'il serait "
    "possible d'organiser une visite.\n\n"
    "Je reste à votre disposition pour échanger.\n\n"
    "Cordialement"
)

# ── Contact par défaut ────────────────────────────────────────────────────────
DEFAULT_PRENOM:    str = "MonPrenom"
DEFAULT_NOM:       str = "MonNom"
DEFAULT_EMAIL:     str = "monemail@email.com"
DEFAULT_TELEPHONE: str = "0600000000"

# ── Colonnes Excel ────────────────────────────────────────────────────────────
EXCEL_COLUMNS: list[str] = [
    "URL",
    "Type de bien",
    "Transaction",
    "Prix affiché",
    "Prix proposé (-35%)",
    "Surface m²",
    "Prix m² bien",
    "Prix m² région min",
    "Prix m² région max",
    "Pièces",
    "Chambres",
    "Étage",
    "Ville",
    "Code postal",
    "DPE",
    "GES",
    "Agence",
    "Téléphone agence",
    "Message envoyé",
    "Date scraping",
    "Statut contact",
]
