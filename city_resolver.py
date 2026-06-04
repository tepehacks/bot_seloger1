"""
city_resolver.py — Resolution dynamique des codes de ville SeLoger.

Utilise Selenium pour interagir avec le formulaire de recherche de SeLoger.com.
Tous les time.sleep() ont ete remplaces par des WebDriverWait + ExpectedConditions.

Optimisations :
  - Cache memoire (dict module) : evite de relancer Selenium pour une ville deja resolue.
  - Cache disque (JSON) : persiste entre les sessions.
  - Timeouts reduits de 10s a 5s par selecteur.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from logger import setup_logger

logger = setup_logger("city_resolver")

# ── Cache ──────────────────────────────────────────────────────────────────────

_CACHE_MEMOIRE: dict[str, str] = {}
_CACHE_FICHIER = Path(__file__).resolve().parent / "data" / "cache_villes.json"


def _charger_cache_disque() -> None:
    """Charge le cache disque dans le cache memoire au demarrage."""
    global _CACHE_MEMOIRE
    try:
        if _CACHE_FICHIER.exists():
            data = json.loads(_CACHE_FICHIER.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _CACHE_MEMOIRE.update(data)
                logger.debug(f"Cache villes charge : {len(_CACHE_MEMOIRE)} entree(s).")
    except Exception as exc:
        logger.debug(f"Cache disque illisible (ignore) : {exc}")


def _sauvegarder_cache_disque() -> None:
    """Sauvegarde le cache memoire sur disque (JSON)."""
    try:
        _CACHE_FICHIER.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FICHIER.write_text(
            json.dumps(_CACHE_MEMOIRE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug(f"Sauvegarde cache disque echouee (ignore) : {exc}")


# Chargement au demarrage du module
_charger_cache_disque()


# ── Selecteurs ─────────────────────────────────────────────────────────────────

# Sélecteurs bases sur le HTML reel de SeLoger (inspecte en juin 2025).
# L'id dynamique (react-aria...) est volontairement exclu car il change a chaque session.
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


# ── Fonction principale ────────────────────────────────────────────────────────

def obtenir_code_ville(ville: str, driver) -> str:
    """
    Navigue sur SeLoger.com, saisit la ville, selectionne la premiere
    suggestion et retourne le code de localisation.

    Utilise un cache memoire + disque pour eviter les requetes repetees.
    Objectif : < 5 secondes pour une ville deja en cache, < 30s sinon.

    Args:
        ville:  Nom de la ville (ex. : "Marseille").
        driver: Instance Selenium WebDriver deja demarree.

    Returns:
        Code SeLoger (ex. : "AD08FR4491").

    Raises:
        ValueError: Si le code ne peut pas etre resolu.
    """
    cle = ville.strip().lower()

    # ── 1. Cache memoire ──────────────────────────────────────────────────────
    if cle in _CACHE_MEMOIRE:
        code_cache = _CACHE_MEMOIRE[cle]
        logger.info(f"Code ville depuis cache memoire : '{ville}' -> {code_cache}")
        return code_cache

    logger.info(f"Resolution du code SeLoger pour : '{ville}'")

    # Retry en cas d'erreur réseau (ERR_NAME_NOT_RESOLVED, timeout réseau...)
    for tentative in range(1, 4):
        try:
            driver.get("https://www.seloger.com")
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            break
        except WebDriverException as exc:
            msg = str(exc)
            if "ERR_NAME_NOT_RESOLVED" in msg or "ERR_INTERNET_DISCONNECTED" in msg or "ERR_CONNECTION" in msg:
                logger.warning(
                    f"Erreur reseau (tentative {tentative}/3) : {msg[:120]}"
                )
                if tentative < 3:
                    time.sleep(3 * tentative)
                else:
                    raise ConnectionError(
                        "Chrome ne peut pas acceder a seloger.com "
                        "(ERR_NAME_NOT_RESOLVED). "
                        "Verifiez la connexion internet et que Chrome n'est pas "
                        "bloque par un antivirus ou un proxy."
                    ) from exc
            else:
                raise

    _fermer_popups(driver)

    # ── 2. Trouver et remplir le champ ville ──────────────────────────────────
    champ = _trouver_champ_ville(driver)
    if champ is None:
        raise ValueError(
            f"Champ de saisie de ville introuvable sur SeLoger pour '{ville}'."
        )

    champ.clear()
    champ.send_keys(ville)

    # Attendre l'apparition des suggestions
    code = _extraire_code_depuis_suggestions(driver, ville)
    if code:
        logger.info(f"Code extrait depuis les suggestions : {code}")
        _mettre_en_cache(cle, code)
        return code

    # ── 3. Fallback : soumettre et extraire depuis l'URL ──────────────────────
    logger.info("Aucun code dans les suggestions — soumission du formulaire...")
    code = _extraire_code_depuis_url(driver, champ)
    if code:
        logger.info(f"Code extrait depuis l'URL : {code}")
        _mettre_en_cache(cle, code)
        return code

    raise ValueError(
        f"Impossible de resoudre le code SeLoger pour '{ville}'. "
        "Verifiez l'orthographe ou reessayez."
    )


def _mettre_en_cache(cle: str, code: str) -> None:
    """Ajoute une entree dans le cache memoire et sauvegarde sur disque."""
    _CACHE_MEMOIRE[cle] = code
    _sauvegarder_cache_disque()
    logger.debug(f"Cache mis a jour : '{cle}' -> {code}")


# ── Helpers prives ─────────────────────────────────────────────────────────────

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
            # Attendre que le popup disparaisse plutot qu'un sleep fixe
            WebDriverWait(driver, 3).until(
                EC.invisibility_of_element_located((by, sel))
            )
            return
        except (TimeoutException, NoSuchElementException):
            continue


def _trouver_champ_ville(driver) -> Optional[object]:
    """
    Cherche le champ de saisie de ville — attend 5 secondes par selecteur
    (reduit de 10s pour limiter le temps total de 40s a 20s dans le pire cas).

    Logs detailles + screenshot si introuvable.
    """
    logger.info("Recherche du champ ville...")

    for sel in _INPUT_SELECTORS:
        try:
            champ = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            logger.info(f"Champ trouve avec le selecteur : {sel}")
            return champ
        except (TimeoutException, NoSuchElementException):
            continue

    # Diagnostic complet si introuvable
    logger.error("Aucun champ ville trouve")
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
    Attend l'apparition des suggestions d'autocompletion avec WebDriverWait,
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
                    # Attendre que l'URL change apres le clic
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
    """Soumet avec Entree et attend le changement d'URL."""
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
    """Extrait le parametre 'locations' d'une URL SeLoger."""
    match = re.search(r"[?&]locations=([^&]+)", url)
    return match.group(1) if match else None


def _ressemble_code_seloger(valeur: str) -> bool:
    """Verifie si une valeur ressemble a un code SeLoger (ex. AD08FR4491)."""
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z]{2}\d+$", valeur))
