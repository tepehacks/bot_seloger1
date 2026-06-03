"""
city_resolver.py — Résolution dynamique des codes de ville SeLoger.

Utilise Selenium pour interagir avec le formulaire de recherche de SeLoger.com.
Tous les time.sleep() ont été remplacés par des WebDriverWait + ExpectedConditions.
"""

import re
from typing import Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from logger import setup_logger

logger = setup_logger("city_resolver")

# Sélecteurs basés sur le HTML réel de SeLoger (inspecté en juin 2025).
# L'id dynamique (react-aria...) est volontairement exclu car il change à chaque session.
_INPUT_SELECTORS = [
    "input[placeholder='Saisir le lieu ou le code postal']",
    "input[aria-autocomplete='list']",
    "input[role='combobox']",
    "input[aria-label='Saisir le lieu ou le code postal']",
]

_SUGGESTION_SELECTORS = [
    "[data-testid='autocomplete-item']",
    "[data-testid='suggestion-item']",
    "li[role='option']",
    ".autocomplete__item",
    ".suggestion-item",
    "[class*='suggestion']",
    "[class*='autocomplete'] li",
    "ul[role='listbox'] li",
]


def obtenir_code_ville(ville: str, driver) -> str:
    """
    Navigue sur SeLoger.com, saisit la ville, sélectionne la première
    suggestion et retourne le code de localisation.

    Args:
        ville:  Nom de la ville (ex. : "Marseille").
        driver: Instance Selenium WebDriver déjà démarrée.

    Returns:
        Code SeLoger (ex. : "AD08FR4491").

    Raises:
        ValueError: Si le code ne peut pas être résolu.
    """
    logger.info(f"Résolution du code SeLoger pour : '{ville}'")

    driver.get("https://www.seloger.com")

    # Attendre que la page soit chargée (remplace time.sleep fixe)
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    _fermer_popups(driver)

    # ── 1. Trouver et remplir le champ ville ──────────────────────────────────
    champ = _trouver_champ_ville(driver)
    if champ is None:
        raise ValueError(
            f"Champ de saisie de ville introuvable sur SeLoger pour '{ville}'."
        )

    champ.clear()
    champ.send_keys(ville)

    # Attendre l'apparition des suggestions (remplace time.sleep(1.5))
    code = _extraire_code_depuis_suggestions(driver, ville)
    if code:
        logger.info(f"Code extrait depuis les suggestions : {code}")
        return code

    # ── 2. Fallback : soumettre et extraire depuis l'URL ──────────────────────
    logger.info("Aucun code dans les suggestions — soumission du formulaire...")
    code = _extraire_code_depuis_url(driver, champ)
    if code:
        logger.info(f"Code extrait depuis l'URL : {code}")
        return code

    raise ValueError(
        f"Impossible de résoudre le code SeLoger pour '{ville}'. "
        "Vérifiez l'orthographe ou réessayez."
    )


# ── Helpers privés ─────────────────────────────────────────────────────────────

def _fermer_popups(driver) -> None:
    """Ferme les popups RGPD sans sleep fixe."""
    selectors = [
        (By.ID, "didomi-notice-agree-button"),
        (By.XPATH, "//button[contains(text(), 'Tout accepter')]"),
        (By.XPATH, "//button[contains(text(), 'Accepter')]"),
        (By.CSS_SELECTOR, "[data-testid='cookie-accept-all']"),
        (By.CSS_SELECTOR, "button[class*='accept']"),
    ]
    for by, sel in selectors:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, sel))
            )
            btn.click()
            # Attendre que le popup disparaisse plutôt qu'un sleep fixe
            WebDriverWait(driver, 3).until(
                EC.invisibility_of_element_located((by, sel))
            )
            return
        except (TimeoutException, NoSuchElementException):
            continue


def _trouver_champ_ville(driver) -> Optional[object]:
    """
    Cherche le champ de saisie de ville — attend 10 secondes par sélecteur.

    Logs détaillés + screenshot si introuvable.
    """
    logger.info("Recherche du champ ville...")

    for sel in _INPUT_SELECTORS:
        try:
            champ = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            logger.info(f"Champ trouvé avec le sélecteur : {sel}")
            return champ
        except (TimeoutException, NoSuchElementException):
            continue

    # Diagnostic complet si introuvable
    logger.error("Aucun champ ville trouvé")
    logger.error(f"URL actuelle     : {driver.current_url}")
    logger.error(f"Titre de la page : {driver.title}")
    try:
        logger.error(f"HTML (1000 chars) :\n{driver.page_source[:1000]}")
    except Exception:
        pass
    try:
        from config import SCREENSHOTS_DIR
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        chemin = SCREENSHOTS_DIR / "champ_ville_introuvable.png"
        driver.save_screenshot(str(chemin))
        logger.error(f"Screenshot : {chemin}")
    except Exception:
        pass

    return None


def _extraire_code_depuis_suggestions(driver, ville: str) -> Optional[str]:
    """
    Attend l'apparition des suggestions d'autocomplétion avec WebDriverWait,
    puis tente d'en extraire le code de localisation.
    """
    for sel in _SUGGESTION_SELECTORS:
        try:
            # Attend les suggestions sans sleep fixe
            suggestions = WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, sel))
            )
            if not suggestions:
                continue

            for suggestion in suggestions[:3]:
                for attr in ("data-id", "data-value", "data-location-id",
                             "data-code", "data-slug", "value"):
                    code = suggestion.get_attribute(attr)
                    if code and _ressemble_code_seloger(code):
                        return code

                try:
                    suggestion.click()
                    # Attendre que l'URL change après le clic (remplace sleep(0.8))
                    url_avant = driver.current_url
                    WebDriverWait(driver, 3).until(
                        lambda d: d.current_url != url_avant
                    )
                    code = _extraire_locations_depuis_url(driver.current_url)
                    if code:
                        return code
                except Exception:
                    pass

        except (TimeoutException, NoSuchElementException):
            continue

    return None


def _extraire_code_depuis_url(driver, champ) -> Optional[str]:
    """Soumet avec Entrée et attend le changement d'URL (remplace sleep(2))."""
    try:
        url_avant = driver.current_url
        champ.send_keys(Keys.RETURN)
        # Attendre que l'URL change
        WebDriverWait(driver, 10).until(
            lambda d: d.current_url != url_avant
        )
        return _extraire_locations_depuis_url(driver.current_url)
    except Exception as exc:
        logger.warning(f"Erreur soumission formulaire : {exc}")
        return None


def _extraire_locations_depuis_url(url: str) -> Optional[str]:
    """Extrait le paramètre 'locations' d'une URL SeLoger."""
    match = re.search(r"[?&]locations=([^&]+)", url)
    return match.group(1) if match else None


def _ressemble_code_seloger(valeur: str) -> bool:
    """Vérifie si une valeur ressemble à un code SeLoger (ex. AD08FR4491)."""
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z]{2}\d+$", valeur))
