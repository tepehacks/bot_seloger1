"""
url_builder.py — Génération d'URL de recherche SeLoger.

Construit l'URL complète à partir des critères utilisateur et du code de
ville récupéré via city_resolver (qui utilise Selenium).
"""

from urllib.parse import urlencode

from config import (
    SELOGER_BASE_URL,
    SELOGER_SEARCH_PATH,
    TRANSACTION_MAP,
    TYPE_LOGEMENT_MAP,
)
from city_resolver import obtenir_code_ville
from logger import setup_logger

logger = setup_logger("url_builder")


def generer_url_seloger(
    ville: str,
    transaction: str,
    type_logement: str,
    budget_max: int,
    surface_min: int,
    pieces_min: int,
    chambres_min: int,
    driver=None,
) -> str:
    """
    Génère l'URL de recherche SeLoger complète.

    Args:
        ville:         Nom de la ville (ex. : "Marseille").
        transaction:   "achat" ou "location".
        type_logement: "appartement", "maison" ou "appartement,maison".
        budget_max:    Prix maximum en euros.
        surface_min:   Surface minimale en m².
        pieces_min:    Nombre minimal de pièces.
        chambres_min:  Nombre minimal de chambres.
        driver:        Instance Selenium WebDriver (requis pour la résolution de ville).

    Returns:
        URL SeLoger complète prête à être ouverte par Selenium.

    Raises:
        ValueError: Si transaction, type_logement ou ville est invalide.
    """
    # ── Validation ────────────────────────────────────────────────────────────
    transaction_key = transaction.lower().strip()
    if transaction_key not in TRANSACTION_MAP:
        raise ValueError(
            f"Transaction '{transaction}' inconnue. "
            f"Valeurs acceptées : {list(TRANSACTION_MAP.keys())}"
        )

    type_key = type_logement.lower().strip()
    if type_key not in TYPE_LOGEMENT_MAP:
        raise ValueError(
            f"Type de logement '{type_logement}' inconnu. "
            f"Valeurs acceptées : {list(TYPE_LOGEMENT_MAP.keys())}"
        )

    if driver is None:
        raise ValueError(
            "Un driver Selenium est requis pour résoudre le code de ville. "
            "Appelez generer_url_seloger() depuis un contexte SeLogerScraper."
        )

    # ── Résolution du code ville via Selenium ─────────────────────────────────
    code_ville = obtenir_code_ville(ville, driver)

    # ── Construction des paramètres ───────────────────────────────────────────
    params: dict[str, str | int] = {
        "distributionTypes":   TRANSACTION_MAP[transaction_key],
        "estateTypes":         TYPE_LOGEMENT_MAP[type_key],
        "locations":           code_ville,
        "priceMax":            budget_max,
        "spaceMin":            surface_min,
        "numberOfRoomsMin":    pieces_min,
        "numberOfBedroomsMin": chambres_min,
    }

    url = f"{SELOGER_BASE_URL}{SELOGER_SEARCH_PATH}?{urlencode(params)}"
    logger.info(f"URL generee : {url}")
    return url
