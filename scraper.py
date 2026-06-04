"""
scraper.py — Scraping des annonces SeLoger avec Selenium.

Rôle limité après optimisation :
  - Démarrer Chrome (headless, sans images, sans notifications).
  - Résoudre la ville + générer l'URL (via city_resolver / url_builder).
  - Collecter les liens /annonces/*.htm depuis toutes les pages de résultats.
  - Exporter les cookies de session pour requests (fetcher.py).
  - Ouvrir les formulaires de contact et envoyer les messages.

L'extraction des données des fiches est déléguée à fetcher.py (requests + BS4).
"""

import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from config import (
    CHROME_PREFS,
    HEADLESS,
    IMPLICIT_WAIT,
    PAGE_LOAD_TIMEOUT,
    SCREENSHOTS_DIR,
    WAIT_MAX,
    WAIT_MIN,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from logger import setup_logger

logger = setup_logger("scraper")


@dataclass
class Annonce:
    """Représente une annonce immobilière extraite de SeLoger."""

    titre:        str = ""
    prix:         str = ""
    surface:      str = ""
    pieces:       str = ""
    localisation: str = ""
    lien:         str = ""
    agence:       str = ""
    proprietaire: str = ""


class SeLogerScraper:
    """
    Gère Chrome et les interactions nécessitant un vrai navigateur :
    recherche, collecte des liens, formulaires de contact.
    """

    def __init__(self) -> None:
        self.driver: Optional[webdriver.Chrome] = None
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    def __enter__(self) -> "SeLogerScraper":
        self._demarrer_navigateur()
        return self

    def __exit__(self, *_) -> None:
        self.fermer()

    def _demarrer_navigateur(self) -> None:
        """Configure et lance Chrome optimisé (headless, sans images)."""
        logger.info("Démarrage de Chrome (headless, sans images)...")
        options = Options()

        # ── Mode headless ─────────────────────────────────────────────────────
        if HEADLESS:
            options.add_argument("--headless=new")

        # ── Taille et stabilité ───────────────────────────────────────────────
        options.add_argument(f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        # ── Stratégie de chargement : eager = DOM prêt, pas besoin d'attendre
        #    les ressources secondaires (images, polices, analytics...)
        options.page_load_strategy = "eager"

        # ── Anti-détection bot ────────────────────────────────────────────────
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

        # ── Réseau : contourne les problèmes DNS en mode headless Windows ────────
        # ERR_NAME_NOT_RESOLVED peut apparaître si Chrome tente d'utiliser un
        # proxy système inexistant ou mal configuré.
        options.add_argument("--no-proxy-server")
        options.add_argument("--disable-features=NetworkService,NetworkServiceInProcess")

        # ── Désactivation des ressources inutiles ─────────────────────────────
        # Images, notifications, géolocalisation, popups → gain de vitesse majeur
        options.add_experimental_option("prefs", CHROME_PREFS)
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-notifications")
        options.add_argument("--blink-settings=imagesEnabled=false")

        # ── Désactivation des animations et timers en arrière-plan ───────────
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--animation-duration-scale=0")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.implicitly_wait(IMPLICIT_WAIT)
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        logger.info("Chrome démarré avec succès.")

    def fermer(self) -> None:
        """Ferme proprement le navigateur."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Navigateur fermé.")
            except WebDriverException:
                pass
            self.driver = None

    # ── Utilitaires ───────────────────────────────────────────────────────────

    def _attendre(self, min_s: float = WAIT_MIN, max_s: float = WAIT_MAX) -> None:
        """Pause aléatoire minimale pour éviter la détection bot."""
        time.sleep(random.uniform(min_s, max_s))

    def _screenshot_erreur(self, nom: str) -> None:
        """Sauvegarde un screenshot lors d'une erreur."""
        if not self.driver:
            return
        try:
            horodatage = time.strftime("%Y%m%d_%H%M%S")
            chemin = SCREENSHOTS_DIR / f"erreur_{nom}_{horodatage}.png"
            self.driver.save_screenshot(str(chemin))
            logger.warning(f"Screenshot : {chemin}")
        except Exception:
            pass

    def _fermer_popups(self) -> None:
        """Ferme les popups RGPD / cookies sans sleep inutile."""
        selectors = [
            (By.ID, "didomi-notice-agree-button"),
            (By.XPATH, "//button[contains(text(), 'Tout accepter')]"),
            (By.XPATH, "//button[contains(text(), 'Accepter')]"),
            (By.XPATH, "//button[contains(@class, 'accept')]"),
            (By.CSS_SELECTOR, "[data-testid='cookie-accept-all']"),
        ]
        for by, selector in selectors:
            try:
                btn = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((by, selector))
                )
                btn.click()
                # Attendre que le popup disparaisse plutôt qu'un sleep fixe
                WebDriverWait(self.driver, 3).until(
                    EC.invisibility_of_element_located((by, selector))
                )
                return
            except (TimeoutException, NoSuchElementException):
                continue

    def exporter_cookies(self) -> dict[str, str]:
        """
        Exporte les cookies de la session Selenium pour les réutiliser
        dans requests.Session (fetcher.py) — évite les blocages anti-bot.

        Returns:
            Dictionnaire {nom: valeur} des cookies courants.
        """
        if not self.driver:
            return {}
        return {c["name"]: c["value"] for c in self.driver.get_cookies()}

    # ── Collecte des liens (Phase 1) ──────────────────────────────────────────

    def collecter_liens(self, url: str) -> list[str]:
        """
        Navigue sur toutes les pages de résultats et collecte uniquement
        les liens /annonces/*.htm, sans ouvrir les fiches.

        Stratégie principale : a[href*='/annonces/'] filtré sur .htm
        Fallback            : anciens sélecteurs de cartes

        Args:
            url: URL de la première page de résultats SeLoger.

        Returns:
            Liste de hrefs uniques.
        """
        logger.info(f"URL générée : {url}")

        try:
            self.driver.get(url)
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except TimeoutException:
            logger.warning("Timeout au chargement de la page de résultats.")

        self._fermer_popups()
        self._attendre()

        tous_liens: list[str] = []
        liens_vus:  set[str]  = set()
        page = 1

        while True:
            logger.info(f"Collecte des liens — page {page}...")
            liens_page = self._extraire_liens_page()

            logger.info(f"Page {page} : {len(liens_page)} lien(s) trouvé(s).")
            for lien in liens_page[:3]:
                logger.info(f"  -> {lien}")

            nouveaux = [l for l in liens_page if l not in liens_vus]
            tous_liens.extend(nouveaux)
            liens_vus.update(nouveaux)

            if not liens_page or not self._aller_page_suivante():
                break

            page += 1
            self._attendre()

        logger.info(f"Total liens collectés : {len(tous_liens)}")
        return tous_liens

    def _extraire_liens_page(self) -> list[str]:
        """
        Stratégie principale : collecte a[href*='/annonces/'] filtrés sur .htm.
        Fallback : anciens sélecteurs de cartes.
        """
        # ── Sauvegarde HTML debug ─────────────────────────────────────────────
        debug_dir = Path(__file__).resolve().parent / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        try:
            (debug_dir / "page_source.html").write_text(
                self.driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass

        # ── Stratégie principale ──────────────────────────────────────────────
        liens: list[str] = []
        vus:   set[str]  = set()

        try:
            elements = self.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='/annonces/']"
            )
            for el in elements:
                href = el.get_attribute("href") or ""
                if "/annonces/" in href and ".htm" in href and href not in vus:
                    liens.append(href)
                    vus.add(href)
        except Exception as exc:
            logger.warning(f"Erreur stratégie principale : {exc}")

        if liens:
            return liens

        # ── Fallback : anciens sélecteurs de cartes ───────────────────────────
        logger.warning("Stratégie principale sans résultat — fallback cartes.")
        return self._extraire_liens_depuis_cartes()

    def _extraire_liens_depuis_cartes(self) -> list[str]:
        """Fallback : extrait les href depuis les anciennes cartes SeLoger."""
        card_selectors = [
            "[data-testid='sl.list-item-link']",
            "article.c-pa-list",
            "li[data-classified-id]",
            ".classified",
        ]
        liens: list[str] = []
        vus:   set[str]  = set()

        for sel in card_selectors:
            try:
                cartes = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if not cartes:
                    continue
                for carte in cartes:
                    try:
                        a_el = carte.find_element(By.CSS_SELECTOR, "a[href]")
                        href = a_el.get_attribute("href") or ""
                        if href and "seloger.com" in href and href not in vus:
                            liens.append(href)
                            vus.add(href)
                    except (NoSuchElementException, StaleElementReferenceException):
                        continue
                if liens:
                    break
            except Exception:
                continue

        if not liens:
            logger.warning("Aucun lien détecté (principale + fallback).")
            self._screenshot_erreur("no_cards")

        return liens

    # ── Pagination ────────────────────────────────────────────────────────────

    def _aller_page_suivante(self) -> bool:
        """Clique sur 'page suivante'. Retourne False si fin de pagination."""
        selectors_next = [
            "[data-testid='gsl.uilib.Paging.nextButton']",
            "a[aria-label='Page suivante']",
            "a[rel='next']",
            ".pagination__next",
            "[class*='next']",
        ]
        for sel in selectors_next:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                btn.click()
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                return True
            except (TimeoutException, NoSuchElementException):
                continue
        return False


# ── Extraction de prix (hybride Selenium) ─────────────────────────────────────

_PRIX_RE = re.compile(r"\d[\d\s\u00A0\u202F]*€")


def _nettoyer_prix(texte: str) -> str:
    """
    Extrait le premier montant en euros et le normalise en entier brut.

    Exemples :
        "495 000 €"    → "495000"
        "1 600 €/mois" → "1600"
    """
    match = _PRIX_RE.search(texte)
    if not match:
        return ""
    brut = match.group(0)
    # Supprimer espaces, nbsp, narrow-nbsp, €, et tout ce qui suit
    return re.sub(r"[\s\u00A0\u202F€]", "", brut)


def extraire_prix(driver: webdriver.Chrome) -> str:
    """
    Détecte le prix d'une annonce SeLoger avec une stratégie hybride à 4 niveaux.

    Priorité 1 : span.css-1ln7jbg        (classe CSS dynamique SeLoger 2024)
    Priorité 2 : div.annonceSpecsListItemPrice
    Priorité 3 : span[aria-hidden='true'] dans le bloc principal
    Priorité 4 : regex euros dans le bloc principal (galerie exclue)

    Loggue la stratégie utilisée, le texte brut et la valeur extraite.
    En cas d'échec total : screenshot + HTML dans debug/.

    Returns:
        Prix nettoyé (ex. "495000"), ou "" si introuvable.
    """
    def _tenter(selecteur: str, contexte=None) -> tuple[str, str]:
        """Cherche le sélecteur et retourne (texte_brut, prix_nettoyé)."""
        racine = contexte if contexte is not None else driver
        try:
            elements = racine.find_elements(By.CSS_SELECTOR, selecteur)
            for el in elements:
                texte = el.text.strip() or el.get_attribute("textContent").strip()
                if not texte:
                    continue
                prix = _nettoyer_prix(texte)
                if prix:
                    return texte, prix
        except Exception:
            pass
        return "", ""

    # ── Priorité 1 : span.css-1ln7jbg ────────────────────────────────────────
    texte, prix = _tenter("span.css-1ln7jbg")
    if prix:
        logger.info(f"[PRIX OK] Strategie: css-1ln7jbg | Texte: {texte!r} | Valeur: {prix}")
        print(f"[PRIX OK] {prix} € (css-1ln7jbg | {texte!r})")
        return prix

    # ── Priorité 2 : div.annonceSpecsListItemPrice ────────────────────────────
    texte, prix = _tenter("div.annonceSpecsListItemPrice")
    if prix:
        logger.info(f"[PRIX OK] Strategie: annonceSpecsListItemPrice | Texte: {texte!r} | Valeur: {prix}")
        print(f"[PRIX OK] {prix} € (annonceSpecsListItemPrice | {texte!r})")
        return prix

    # ── Priorité 3 : span[aria-hidden='true'] dans le bloc principal ──────────
    bloc_selectors = [
        "main",
        "[data-testid='classified-description']",
        "[class*='classified']",
        "article",
        "body",
    ]
    bloc = None
    for sel in bloc_selectors:
        try:
            bloc = driver.find_element(By.CSS_SELECTOR, sel)
            break
        except NoSuchElementException:
            continue

    if bloc:
        texte, prix = _tenter("span[aria-hidden='true']", contexte=bloc)
        if prix:
            logger.info(f"[PRIX OK] Strategie: aria-hidden | Texte: {texte!r} | Valeur: {prix}")
            print(f"[PRIX OK] {prix} € (aria-hidden | {texte!r})")
            return prix

    # ── Priorité 4 : regex dans le texte du bloc principal ───────────────────
    try:
        contenu = (bloc or driver.find_element(By.TAG_NAME, "body")).text
        match = _PRIX_RE.search(contenu)
        if match:
            texte = match.group(0)
            prix = _nettoyer_prix(texte)
            if prix:
                logger.info(f"[PRIX OK] Strategie: regex fallback | Texte: {texte!r} | Valeur: {prix}")
                print(f"[PRIX OK] {prix} € (regex fallback | {texte!r})")
                return prix
    except Exception:
        pass

    # ── Échec total ───────────────────────────────────────────────────────────
    logger.warning("[PRIX KO] Prix introuvable — toutes les strategies ont echoue.")
    print("[PRIX KO] Prix introuvable")
    try:
        horodatage = time.strftime("%Y%m%d_%H%M%S")
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(SCREENSHOTS_DIR / f"prix_manquant_{horodatage}.png"))
        debug_dir = Path(__file__).resolve().parent / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"prix_manquant_{horodatage}.html").write_text(
            driver.page_source, encoding="utf-8"
        )
        print(f"[PRIX KO] Screenshot + HTML sauvegardes ({horodatage})")
    except Exception:
        pass

    return ""
