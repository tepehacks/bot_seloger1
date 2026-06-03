"""
utils.py — Fonctions utilitaires Selenium robustes.

Fournit :
  - capturer_erreur()   : screenshot + HTML horodates automatiques
  - logger_erreur()     : log complet URL / titre / type / stacktrace
  - safe_find_element() : essaie plusieurs selecteurs, retourne None si echec
  - safe_click()        : scroll + click + fallback JS + retry
  - safe_send_keys()    : clear + click + send_keys + verification + retry
"""

import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidSelectorException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import SCREENSHOTS_DIR
from logger import setup_logger


def safe_log(text) -> str:
    """Convertit n'importe quel texte en str sans jamais lever d'exception."""
    try:
        return str(text)
    except Exception:
        return repr(text)

logger = setup_logger("utils")

_DEBUG_DIR = Path(__file__).resolve().parent / "debug"

# Toutes les exceptions Selenium gerees de facon centralisee
SELENIUM_EXCEPTIONS = (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidSelectorException,
    WebDriverException,
    Exception,
)


# ── Capture d'erreur ──────────────────────────────────────────────────────────

def capturer_erreur(driver, nom: str = "error") -> None:
    """
    Sauvegarde automatiquement :
      - screenshots/error_YYYYMMDD_HHMMSS.png
      - debug/error_YYYYMMDD_HHMMSS.html
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        chemin_png = SCREENSHOTS_DIR / f"{nom}_{ts}.png"
        driver.save_screenshot(str(chemin_png))
        logger.info(f"Screenshot erreur : {chemin_png.name}")
    except Exception:
        pass
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        chemin_html = _DEBUG_DIR / f"{nom}_{ts}.html"
        chemin_html.write_text(driver.page_source, encoding="utf-8")
        logger.info(f"HTML debug erreur : {chemin_html.name}")
    except Exception:
        pass


def logger_erreur(driver, exc: Exception, contexte: str = "") -> None:
    """Logue URL, titre, type d'erreur, message et stacktrace completes."""
    try:
        logger.error(f"[ERREUR] {contexte}")
        logger.error(f"  URL    : {driver.current_url}")
        logger.error(f"  Titre  : {driver.title}")
        logger.error(f"  Type   : {type(exc).__name__}")
        logger.error(f"  Msg    : {exc}")
        logger.error(f"  Stack  :\n{traceback.format_exc()}")
    except Exception:
        pass


# ── Fonctions robustes ────────────────────────────────────────────────────────

def safe_find_element(driver, selecteurs: list[tuple], timeout: int = 5):
    """
    Essaie plusieurs selecteurs dans l'ordre.
    Retourne le premier element trouve, ou None si aucun ne fonctionne.
    Ne fait jamais planter le programme.

    Exemple :
        el = safe_find_element(driver, [
            (By.NAME,       "firstName"),
            (By.CSS_SELECTOR, "input[name='firstName']"),
            (By.XPATH,      "//input[@name='firstName']"),
        ])
    """
    for i, (by, sel) in enumerate(selecteurs, start=1):
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, sel))
            )
            logger.debug(f"safe_find_element : trouve avec selecteur {i} ({sel})")
            return el
        except (TimeoutException, NoSuchElementException,
                InvalidSelectorException, Exception):
            continue
    logger.debug(f"safe_find_element : aucun des {len(selecteurs)} selecteurs n'a fonctionne.")
    return None


def safe_click(driver, element, retries: int = 3) -> bool:
    """
    Scroll vers l'element + clic Selenium, fallback clic JS.
    3 tentatives maximum avec attente progressive (1s, 2s, 3s).
    Retourne True si le clic a reussi, False sinon.
    """
    for attempt in range(1, retries + 1):
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element
            )
            try:
                element.click()
                logger.debug(f"safe_click : clic Selenium reussi (tentative {attempt}).")
                return True
            except (ElementClickInterceptedException,
                    ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", element)
                logger.debug(f"safe_click : clic JS reussi (tentative {attempt}).")
                return True
        except SELENIUM_EXCEPTIONS as exc:
            logger.warning(f"safe_click tentative {attempt}/{retries} echouee : {exc}")
            if attempt < retries:
                time.sleep(attempt)  # 1s, 2s, 3s
    logger.error("safe_click : toutes les tentatives ont echoue.")
    return False


def safe_send_keys(driver, element, valeur: str, retries: int = 3) -> bool:
    """
    Clear + click + send_keys avec verification de la valeur saisie.
    3 tentatives maximum avec attente progressive (1s, 2s, 3s).
    Retourne True si la valeur a ete correctement saisie, False sinon.
    """
    for attempt in range(1, retries + 1):
        try:
            element.click()
            element.send_keys(Keys.CONTROL + "a")
            element.send_keys(Keys.DELETE)
            element.send_keys(valeur)
            # Verification que la valeur a bien ete saisie
            valeur_saisie = element.get_attribute("value") or ""
            if valeur_saisie:
                logger.debug(
                    f"safe_send_keys : valeur saisie OK (tentative {attempt})."
                )
                return True
        except SELENIUM_EXCEPTIONS as exc:
            logger.warning(
                f"safe_send_keys tentative {attempt}/{retries} echouee : {exc}"
            )
            if attempt < retries:
                time.sleep(attempt)  # 1s, 2s, 3s
    logger.error(f"safe_send_keys : echec apres {retries} tentatives.")
    return False
