"""
contact_manager.py — Remplissage et envoi du formulaire de contact SeLoger.

Corrections appliquees :
  - firstName/lastName/email : ciblage du DERNIER champ (formulaire contact,
    pas le formulaire profil pre-existant).
  - phoneNumber : type='hidden' → remplissage du champ visible adjacent + JS.
  - Usercentrics : MutationObserver installe au chargement de page + suppression
    JS systematique avant chaque clic critique.
  - Unicode : labels de log en ASCII pur.
"""

import json
import random
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import SCREENSHOTS_DIR, WAIT_MAX, WAIT_MIN
from logger import setup_logger
from scraper import Annonce, SeLogerScraper, extraire_prix
from utils import capturer_erreur, logger_erreur, safe_click, safe_find_element, safe_log

logger = setup_logger("contact_manager")

_DEBUG_DIR = Path(__file__).resolve().parent / "debug"


def _generer_message(prix_str: str) -> str:
    """
    Genere le message de contact.
    Si le prix est disponible, inclut une offre a -35%.
    Sinon, retourne le message standard.
    """
    prix_brut = re.sub(r"[^\d]", "", prix_str or "")
    if prix_brut:
        try:
            prix_base = int(prix_brut)
            prix_offre = int(prix_base * 0.65)
            # Formatage avec separateur de milliers
            prix_offre_fmt = f"{prix_offre:,}".replace(",", " ")
            return (
                "Bonjour,\n\n"
                "Votre bien a retenu toute mon attention.\n\n"
                f"Je me permets de vous soumettre une offre d'achat a {prix_offre_fmt} €.\n\n"
                "Je reste a votre disposition pour en discuter et organiser une visite.\n\n"
                "Cordialement"
            )
        except ValueError:
            pass

    return (
        "Bonjour,\n\n"
        "Votre bien a retenu toute mon attention.\n\n"
        "Je souhaiterais savoir s'il est toujours disponible et s'il serait "
        "possible d'organiser une visite.\n\n"
        "Je reste a votre disposition pour echanger.\n\n"
        "Cordialement"
    )

def _extraire_etage(soup, texte_page: str) -> str:
    """
    Extrait l'étage de l'appartement depuis plusieurs sources SeLoger.

    Priorité :
      1. data-testid='cdp-features' — "Dernier étage, 3ème étage/3 étages"
      2. Keyfacts — "Étage 3/3"
      3. Regex texte — "au 3e étage", "3ème étage", "au 3e et dernier étage"
      4. RDC
    """
    def _formater(n: int, dernier: bool = False) -> str:
        suffix = "er" if n == 1 else "ème"
        base = f"{n}{suffix} étage"
        return f"{base} (dernier)" if dernier else base

    # Priorité 1 : data-testid='cdp-features'
    # Contient "Dernier étage, 3ème étage/3 étages" ou "3ème étage/3 étages"
    try:
        features_el = soup.find(attrs={"data-testid": "cdp-features"})
        if features_el:
            texte_feat = features_el.get_text(" ", strip=True)
            # "Dernier étage, 3ème étage/3 étages"
            m = re.search(r'[Dd]ernier\s+[eé]tage[,\s]+(\d+)[eè]me?\s+[eé]tage', texte_feat, re.I)
            if m:
                return _formater(int(m.group(1)), dernier=True)
            # "3ème étage/3 étages"
            m = re.search(r'(\d+)[eè]me?\s+[eé]tage\s*/\s*\d+', texte_feat, re.I)
            if m:
                return _formater(int(m.group(1)))
            # "Dernier étage" seul
            if re.search(r'[Dd]ernier\s+[eé]tage', texte_feat):
                return "Dernier étage"
    except Exception:
        pass

    # Priorité 2 : keyfacts "Étage X/Y"
    m = re.search(r'[Éé]tage\s+(\d+)/(\d+)', texte_page)
    if m:
        floor = int(m.group(1))
        total = int(m.group(2))
        return _formater(floor, dernier=(floor == total))

    # Priorité 3 : regex texte
    # "au 3e étage", "au 3ème étage", "au 3e et dernier étage"
    m = re.search(r'au\s+(\d+)\s*(?:er|[eè]me?|ième?)?\s*(?:et\s+dernier\s+)?[eé]tage', texte_page, re.I)
    if m:
        return _formater(int(m.group(1)), dernier=bool(re.search(r'dernier', m.group(0), re.I)))
    # "3ème étage" sans "au"
    m = re.search(r'\b(\d+)\s*(?:er|[eè]me?|ième?)\s+[eé]tage', texte_page, re.I)
    if m:
        return _formater(int(m.group(1)))

    # Priorité 4 : RDC
    if re.search(r'rez[.\s-]*de[.\s-]*chauss[eé]e|RDC|\brez\b', texte_page, re.I):
        return "RDC"

    return ""


_MOTS_CLES_CONTACT = [
    "Contacter", "Contact", "Envoyer", "Message",
    "Je suis interesse", "Demande", "Prendre contact", "Ecrire",
]

# JS : MutationObserver qui supprime Usercentrics des qu'il apparait dans le DOM
_JS_BLOQUEUR_USERCENTRICS = """
(function() {
    if (window._ucObserverInstalled) return;
    window._ucObserverInstalled = true;
    function supprimerUC() {
        const uc = document.getElementById('usercentrics-root');
        if (uc) uc.remove();
        document.querySelectorAll("[id*='usercentrics'],[class*='usercentrics']")
            .forEach(e => e.remove());
        document.body.style.overflow = 'auto';
    }
    supprimerUC();
    const obs = new MutationObserver(supprimerUC);
    obs.observe(document.documentElement, { childList: true, subtree: true });
    window._ucObserver = obs;
})();
"""

# JS : remplissage d'un champ React en declenchant les evenements natifs
_JS_REMPLIR_CHAMP = """
const el = arguments[0];
const val = arguments[1];
const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value'
).set;
setter.call(el, val);
el.dispatchEvent(new Event('input',  { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
"""


# ── Fonctions autonomes ───────────────────────────────────────────────────────

def fermer_popup_usercentrics(driver) -> bool:
    """
    Detecte et supprime definitivement la popup Usercentrics.

    1. Cherche #usercentrics-root / [id*='usercentrics'] / [class*='usercentrics'].
    2. Tente de cliquer sur OK / Accepter / Tout accepter /
       Continuer sans accepter.
    3. Si encore presente : suppression JS complete + restauration du scroll.
    """
    popup_presente = False
    for sel in [
        "#usercentrics-root",
        "[id*='usercentrics']",
        "[class*='usercentrics']",
    ]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                popup_presente = True
                break
        except NoSuchElementException:
            continue

    if not popup_presente:
        return True

    logger.info("Popup Usercentrics detectee.")

    boutons_candidats = [
        (By.XPATH, "//button[contains(text(),'OK')]"),
        (By.XPATH, "//button[contains(text(),'Accepter')]"),
        (By.XPATH, "//button[contains(text(),'Tout accepter')]"),
        (By.XPATH, "//a[contains(text(),'Continuer sans accepter')]"),
    ]

    for by, sel in boutons_candidats:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, sel))
            )
            texte_btn = (btn.text or "").strip()
            btn.click()
            logger.info(f"Bouton utilise : '{texte_btn}'")
            try:
                WebDriverWait(driver, 5).until(
                    lambda d: not _usercentrics_visible(d)
                )
                logger.info("Popup Usercentrics fermee.")
                return True
            except TimeoutException:
                logger.warning("Popup toujours presente apres clic.")
                break
        except (TimeoutException, NoSuchElementException,
                ElementNotInteractableException, ElementClickInterceptedException):
            continue

    # Suppression JS complete
    if _usercentrics_visible(driver):
        logger.warning("Suppression JS de Usercentrics.")
        try:
            driver.execute_script("""
                const uc = document.getElementById('usercentrics-root');
                if (uc) uc.remove();
                document.querySelectorAll("[id*='usercentrics']")
                    .forEach(e => e.remove());
                document.querySelectorAll("[class*='usercentrics']")
                    .forEach(e => e.remove());
                document.body.style.overflow = 'auto';
            """)
            logger.info("Popup Usercentrics supprimee via JS.")
            return True
        except Exception as exc:
            logger.warning(f"Suppression JS echouee : {exc}")
            return False

    return True


def _usercentrics_visible(driver) -> bool:
    for sel in ["#usercentrics-root", "[id*='usercentrics']"]:
        try:
            if driver.find_element(By.CSS_SELECTOR, sel).is_displayed():
                return True
        except NoSuchElementException:
            continue
    return False


# ── Classe principale ─────────────────────────────────────────────────────────

class ContactManager:

    def __init__(
        self,
        scraper: SeLogerScraper,
        prenom: str,
        nom: str,
        email: str,
        telephone: str,
    ) -> None:
        self.scraper   = scraper
        self.driver    = scraper.driver
        self.prenom    = prenom
        self.nom       = nom
        self.email     = email
        self.telephone = telephone

    # ── Interface publique ────────────────────────────────────────────────────

    def contacter_annonce(
        self,
        annonce: Annonce,
        envoyer_message: bool = True,
    ) -> tuple[bool, str]:
        if not annonce.lien:
            return False, "Lien manquant"

        logger.info(f"Contact : {annonce.titre or annonce.lien}")

        try:
            self._ouvrir_fiche(annonce.lien)
            # Enrichir les donnees depuis le HTML rendu par JS (Selenium)
            self._enrichir_annonce(annonce)
            self._chercher_et_cliquer_bouton_contact()
            # Debug APRES le clic — le HTML contient maintenant le formulaire rendu
            self._debug_page("formulaire")
            self._logger_tous_champs()

            mode = self._detecter_mode_formulaire()
            if mode is None:
                logger.warning("Aucun formulaire detecte.")
                return False, "Pas de formulaire"

            self._debug_avant_remplissage()
            self._logger_tous_champs()
            self._remplir_formulaire(annonce.prix, mode=mode, annonce=annonce)

            if envoyer_message:
                self._cliquer_envoyer()
                logger.info(f"Message envoye : {annonce.titre or annonce.lien}")
                return True, "Contacte"
            else:
                logger.info("Simulation — formulaire rempli, envoi non effectue.")
                return True, "Simule"

        except TimeoutException as exc:
            logger_erreur(self.driver, exc, f"Timeout : {annonce.lien}")
            capturer_erreur(self.driver, "error")
            return False, "Erreur Timeout"
        except Exception as exc:
            logger_erreur(self.driver, exc, f"Erreur contact : {annonce.lien}")
            capturer_erreur(self.driver, "error")
            return False, f"Erreur : {type(exc).__name__}"

    # ── Enrichissement des donnees depuis la page rendue ─────────────────────

    def _enrichir_annonce(self, annonce) -> None:
        """
        Extrait titre, prix, surface, pieces, localisation, agence depuis
        le HTML rendu par Selenium (JS execute) et met a jour l'annonce.
        Strategies :
          1. __NEXT_DATA__ (Next.js) — JSON complet injecte par SeLoger
          2. JSON-LD <script type='application/ld+json'>
          3. Selecteurs CSS sur le DOM rendu
        """
        try:
            soup = BeautifulSoup(self.driver.page_source, "lxml")
        except Exception:
            return

        # ── Strategie 0 : URL — type_bien et transaction fiables a 100% ────────
        # Pattern SeLoger : /annonces/(achat|location)/(appartement|maison|...)/
        m_url = re.search(r'/annonces/(achat|location)/([^/]+)/', annonce.lien or "", re.I)
        if m_url:
            tx_raw   = m_url.group(1).lower()
            type_raw = m_url.group(2).lower()
            if not annonce.transaction:
                annonce.transaction = "Achat" if tx_raw == "achat" else "Location"
            if not annonce.type_bien:
                _TYPE_MAP = {
                    "appartement": "Appartement",
                    "maison":       "Maison",
                    "studio":       "Studio",
                    "loft":         "Loft",
                    "duplex":       "Duplex",
                    "villa":        "Villa",
                    "terrain":      "Terrain",
                    "local":        "Local commercial",
                    "bureau":       "Bureau",
                    "parking":      "Parking",
                }
                annonce.type_bien = _TYPE_MAP.get(type_raw, type_raw.capitalize())

        # ── Strategie 1 : __NEXT_DATA__ (Next.js) ────────────────────────────
        next_script = soup.find("script", id="__NEXT_DATA__")
        if next_script:
            try:
                data = json.loads(next_script.string or "")
                props = data.get("props", {}).get("pageProps", {})
                # SeLoger place les donnees sous differentes cles selon la version
                classified = (
                    props.get("classified")
                    or props.get("listing")
                    or props.get("annonce")
                    or props.get("data", {}).get("classified")
                    or {}
                )
                if classified:
                    if not annonce.titre:
                        annonce.titre = str(
                            classified.get("title")
                            or classified.get("titre")
                            or classified.get("name")
                            or ""
                        )
                    if not annonce.type_bien:
                        t = (
                            classified.get("estateType")
                            or classified.get("propertyType")
                            or classified.get("typeLogement")
                            or classified.get("type")
                            or ""
                        )
                        if t:
                            annonce.type_bien = str(t)
                    if not annonce.transaction:
                        tx = (
                            classified.get("transactionType")
                            or classified.get("distributionType")
                            or classified.get("transaction")
                            or ""
                        )
                        if tx:
                            annonce.transaction = str(tx)
                    if not annonce.prix:
                        prix = (
                            classified.get("price")
                            or classified.get("prix")
                            or classified.get("priceRaw")
                        )
                        if prix:
                            annonce.prix = str(prix)
                    if not annonce.surface:
                        surface = (
                            classified.get("surface")
                            or classified.get("area")
                            or classified.get("livingArea")
                        )
                        if surface:
                            annonce.surface = str(surface)
                    if not annonce.pieces:
                        pieces = (
                            classified.get("roomsQuantity")
                            or classified.get("rooms")
                            or classified.get("pieces")
                            or classified.get("nbRooms")
                        )
                        if pieces:
                            annonce.pieces = str(pieces)
                    if not annonce.chambres:
                        ch = (
                            classified.get("bedroomsQuantity")
                            or classified.get("bedrooms")
                            or classified.get("nbBedrooms")
                            or classified.get("numberOfBedrooms")
                        )
                        if ch:
                            annonce.chambres = str(ch)
                    if not annonce.etage:
                        fl = (
                            classified.get("floor")
                            or classified.get("floorNumber")
                            or classified.get("floorLevel")
                            or classified.get("etage")
                        )
                        if fl is not None:
                            annonce.etage = str(fl)
                    if not annonce.localisation:
                        loc = classified.get("location", {})
                        if isinstance(loc, dict):
                            annonce.localisation = (
                                loc.get("city")
                                or loc.get("ville")
                                or loc.get("locality")
                                or ""
                            )
                        elif isinstance(loc, str):
                            annonce.localisation = loc
                    if not annonce.agence:
                        agency = classified.get("agency", {}) or classified.get("agence", {})
                        if isinstance(agency, dict):
                            annonce.agence = agency.get("name") or agency.get("nom") or ""
                        elif isinstance(agency, str):
                            annonce.agence = agency
            except Exception:
                pass

        # ── Strategie 2 : JSON-LD ─────────────────────────────────────────────
        if not annonce.titre:
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    schemas = data if isinstance(data, list) else [data]
                    for s in schemas:
                        if not annonce.titre:
                            annonce.titre = s.get("name") or s.get("headline") or ""
                        if not annonce.prix:
                            offers = s.get("offers", {})
                            if isinstance(offers, dict):
                                p = offers.get("price") or offers.get("lowPrice")
                                if p:
                                    annonce.prix = str(p)
                        if not annonce.surface:
                            fs = s.get("floorSize", {})
                            v = fs.get("value") if isinstance(fs, dict) else fs
                            if v:
                                annonce.surface = str(v)
                        if not annonce.pieces:
                            annonce.pieces = str(s.get("numberOfRooms", ""))
                        if not annonce.localisation:
                            addr = s.get("address", {})
                            if isinstance(addr, dict):
                                annonce.localisation = (
                                    addr.get("addressLocality")
                                    or addr.get("streetAddress")
                                    or ""
                                )
                except Exception:
                    continue

        # ── Strategie 3 : selecteurs CSS sur DOM rendu ────────────────────────
        def _txt(selectors):
            for sel in selectors:
                try:
                    el = soup.select_one(sel)
                    if el:
                        t = el.get_text(strip=True)
                        if t:
                            return t
                except Exception:
                    continue
            return ""

        if not annonce.titre:
            annonce.titre = _txt(["h1", "[data-testid='classified-title']",
                                   "[class*='title']"])
        if not annonce.prix:
            annonce.prix = _txt(["[data-testid='price']", "[class*='price']",
                                  "[class*='Price']", "[class*='prix']"])
        if not annonce.surface:
            annonce.surface = _txt(["[data-testid='surface']", "[class*='surface']"])
        if not annonce.pieces:
            annonce.pieces = _txt(["[data-testid='rooms']", "[class*='room']",
                                    "[class*='piece']"])
        if not annonce.localisation:
            annonce.localisation = _txt(["[data-testid='city']", "[class*='city']",
                                          "[class*='location']"])

        # Fallback localisation : extraire depuis l'URL ou le titre
        if not annonce.localisation and annonce.titre:
            m = re.search(r"[àa]\s+([\w\s\-]+?)(?:\s*[\|\-]|$)", annonce.titre, re.I)
            if m:
                annonce.localisation = m.group(1).strip()

        # ── Extraction avancée depuis le texte de la page ─────────────────────
        texte_page = soup.get_text(" ", strip=True)

        # Type de bien : depuis le titre "Appartement", "Maison", "Studio"...
        if not annonce.type_bien:
            for mot, label in [
                ("studio",      "Studio"),
                ("appartement", "Appartement"),
                ("maison",      "Maison"),
                ("villa",       "Villa"),
                ("loft",        "Loft"),
                ("duplex",      "Duplex"),
                ("terrain",     "Terrain"),
                ("local",       "Local commercial"),
                ("bureau",      "Bureau"),
            ]:
                if re.search(mot, annonce.titre or texte_page[:200], re.I):
                    annonce.type_bien = label
                    break

        # Transaction : depuis le titre ou texte
        if not annonce.transaction:
            if re.search(r'\bvente\b|\bachat\b|\bvendre\b|\ba vendre\b', texte_page[:300], re.I):
                annonce.transaction = "Achat"
            elif re.search(r'\blocation\b|\blouer\b|\ba louer\b', texte_page[:300], re.I):
                annonce.transaction = "Location"

        # Code postal : "(52120)" dans la localisation ou le texte de la page
        if not annonce.code_postal:
            for source in [annonce.localisation, annonce.titre, texte_page[:500]]:
                m = re.search(r'\((\d{5})\)', source or "")
                if m:
                    annonce.code_postal = m.group(1)
                    if source == annonce.localisation:
                        annonce.localisation = re.sub(
                            r'\s*\(\d{5}\)', '', annonce.localisation
                        ).strip()
                    break

        # Pièces : "X pièce(s)" dans le texte
        if not annonce.pieces:
            m = re.search(r'(\d+)\s*pi[eè]ce', texte_page, re.I)
            if m:
                annonce.pieces = m.group(1)

        # Chambres : "X chambre(s)" dans le texte
        if not annonce.chambres:
            m = re.search(r'(\d+)\s*chambre', texte_page, re.I)
            if m:
                annonce.chambres = m.group(1)

        # Étage de l'appartement (pas le nb d'étages du bâtiment)
        if not annonce.etage:
            annonce.etage = _extraire_etage(soup, texte_page)

        # Surface : fallback depuis "X m²" dans le texte
        if not annonce.surface:
            m = re.search(r'(\d+)\s*m[²2]', texte_page)
            if m:
                annonce.surface = m.group(1)

        # Prix m² du bien
        if not annonce.prix_m2:
            m = re.search(
                r'([\d\s\u00A0\u202F]+[,.]?\d*)\s*€\s*/\s*m[²2]',
                texte_page
            )
            if m:
                brut = m.group(1).strip()
                annonce.prix_m2 = re.sub(r'[\s\u00A0\u202F]', '', brut).replace(',', '.')

        # Prix m² région min et max
        # Structure réelle SeLoger :
        #   "Valeur la plus basse de la région"  → nombre juste après
        #   "Valeur la plus élevée de la région" → nombre juste après
        def _extraire_valeur_apres_label(label: str, texte: str) -> str:
            m = re.search(
                re.escape(label) + r'[\s\S]{0,60}?([\d\s\u00A0\u202F]+[,.]?\d*)\s*€\s*/\s*m[²2]',
                texte, re.I
            )
            if m:
                brut = m.group(1).strip()
                return re.sub(r'[\s\u00A0\u202F]', '', brut).replace(',', '.')
            return ""

        if not annonce.prix_m2_region_min:
            annonce.prix_m2_region_min = _extraire_valeur_apres_label(
                "Valeur la plus basse de la région", texte_page
            )
        if not annonce.prix_m2_region_max:
            annonce.prix_m2_region_max = _extraire_valeur_apres_label(
                "Valeur la plus élevée de la région", texte_page
            )

        # DPE et GES — via data-testid='cdp-preview-scale-highlighted'
        # Structure SeLoger : premier bloc = DPE, second bloc = GES
        # Chaque bloc contient un h3 avec le titre et un élément highlighted avec la lettre
        dpe_ges_els = soup.find_all(attrs={"data-testid": "cdp-preview-scale-highlighted"})
        if len(dpe_ges_els) >= 1 and not annonce.dpe:
            lettre = dpe_ges_els[0].get_text(strip=True).upper()
            if re.match(r'^[A-G]$', lettre):
                annonce.dpe = lettre
        if len(dpe_ges_els) >= 2 and not annonce.ges:
            lettre = dpe_ges_els[1].get_text(strip=True).upper()
            if re.match(r'^[A-G]$', lettre):
                annonce.ges = lettre
        # Fallback regex si data-testid absent
        if not annonce.dpe:
            m = re.search(r'DPE\s+Classe\s+([A-G])', texte_page, re.I)
            if not m:
                m = re.search(r'Diagnostic de performance[^A-G]{0,200}([A-G])\b', texte_page, re.I | re.S)
            if m:
                annonce.dpe = m.group(1).upper()
        if not annonce.ges:
            m = re.search(r'GES\s+Classe\s+([A-G])', texte_page, re.I)
            if not m:
                m = re.search(r"[Ii]ndice d.émission[^A-G]{0,200}([A-G])\b", texte_page, re.I | re.S)
            if m:
                annonce.ges = m.group(1).upper()

        # Téléphone agence — clic sur "Appeler" pour révéler le numéro
        if not annonce.telephone_agence:
            annonce.telephone_agence = self._extraire_telephone_agence()

        # Fallback prix : extraire via Selenium (4 strategies hybrides)
        if not annonce.prix:
            annonce.prix = extraire_prix(self.driver)

        # Prix proposé = prix de base × 0.65 (-35%)
        if not annonce.prix_propose and annonce.prix:
            prix_brut = re.sub(r'[^\d]', '', annonce.prix)
            if prix_brut:
                try:
                    prix_propose = int(int(prix_brut) * 0.65)
                    annonce.prix_propose = f"{prix_propose:,}".replace(",", " ")
                except ValueError:
                    pass

        logger.info(
            f"Annonce enrichie : type={annonce.type_bien} | tx={annonce.transaction} | "
            f"prix={annonce.prix} | offre={annonce.prix_propose} | "
            f"surface={annonce.surface} | pieces={annonce.pieces} | "
            f"chambres={annonce.chambres} | etage={annonce.etage} | "
            f"ville={annonce.localisation} ({annonce.code_postal}) | "
            f"m²={annonce.prix_m2} | region=[{annonce.prix_m2_region_min}-{annonce.prix_m2_region_max}] | "
            f"DPE={annonce.dpe} GES={annonce.ges}"
        )

    # ── Extraction téléphone agence ───────────────────────────────────────────

    def _extraire_telephone_agence(self) -> str:
        """
        Clique sur le bouton 'Appeler' pour révéler le numéro de l'agence,
        puis extrait le numéro affiché.

        SeLoger charge le numéro dynamiquement via JS lors du clic.
        Sélecteur stable : data-testid='cdp-contacting-call-button'
        """
        try:
            btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "[data-testid='cdp-contacting-call-button']")
                )
            )
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn
            )
            self.driver.execute_script("arguments[0].click();", btn)
            logger.info("Clic bouton 'Appeler' effectue.")

            # Attendre que le numéro apparaisse dans le bouton ou un élément adjacent
            time.sleep(1.5)

            # Chercher le numéro dans la page après le clic
            # SeLoger remplace le bouton ou affiche le numéro dans un span/div voisin
            for sel in [
                "[data-testid='cdp-contacting-call-button']",
                "[data-testid='aviv.CDP.Contacting.ProviderSection.ContactCard.PhoneButton']",
                "a[href^='tel:']",
            ]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    # Cas 1 : lien tel:
                    href = el.get_attribute("href") or ""
                    if href.startswith("tel:"):
                        tel = href.replace("tel:", "").strip()
                        logger.info(f"Telephone agence (tel: href) : {tel}")
                        return tel
                    # Cas 2 : texte du bouton a changé
                    texte = (el.text or "").strip()
                    m = re.search(r'(?:\+33|0)[1-9](?:[\s\.\-]?\d{2}){4}', texte)
                    if m:
                        tel = re.sub(r'[\s\.\-]', '', m.group(0))
                        logger.info(f"Telephone agence (texte bouton) : {tel}")
                        return tel
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            # Dernière chance : regex dans le texte de la page entière
            try:
                texte_page = self.driver.find_element(By.TAG_NAME, "body").text
                m = re.search(
                    r'(?:\+33\s?|0)[1-9](?:[\s\.\-]?\d{2}){4}',
                    texte_page[texte_page.find("Appeler"):texte_page.find("Appeler") + 300]
                    if "Appeler" in texte_page else ""
                )
                if m:
                    tel = re.sub(r'[\s\.\-]', '', m.group(0))
                    logger.info(f"Telephone agence (regex page) : {tel}")
                    return tel
            except Exception:
                pass

        except (TimeoutException, NoSuchElementException):
            logger.info("Bouton 'Appeler' absent — telephone agence non disponible.")
        except Exception as exc:
            logger.warning(f"Erreur extraction telephone agence : {exc}")

        return ""

    # ── Navigation ────────────────────────────────────────────────────────────

    def _ouvrir_fiche(self, lien: str) -> None:
        """Ouvre la fiche et installe immediatement le bloqueur Usercentrics."""
        self.driver.get(lien)
        WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        # Installer le MutationObserver anti-Usercentrics des le chargement
        try:
            self.driver.execute_script(_JS_BLOQUEUR_USERCENTRICS)
            logger.info("Bloqueur Usercentrics installe.")
        except Exception:
            pass
        # Appel de securite en plus
        fermer_popup_usercentrics(self.driver)
        self.scraper._attendre(0.8, 1.5)

    # ── Debug ─────────────────────────────────────────────────────────────────

    def _debug_page(self, nom: str) -> None:
        logger.info(f"[DEBUG] URL   : {self.driver.current_url}")
        logger.info(f"[DEBUG] Titre : {self.driver.title}")
        try:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            self.driver.save_screenshot(str(SCREENSHOTS_DIR / f"screenshot_{nom}.png"))
        except Exception:
            pass
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (_DEBUG_DIR / f"page_{nom}.html").write_text(
                self.driver.page_source, encoding="utf-8"
            )
        except Exception:
            pass

    def _chercher_et_cliquer_bouton_contact(self) -> None:
        """
        Cherche le bouton de contact et clique dessus.
        Priorite aux <button> pour eviter de cliquer sur des <a> qui naviguent.
        Si la page part vers /map apres le clic, revient en arriere.
        """
        url_avant = self.driver.current_url

        # Strategies : <button> en priorite, jamais de //* generique
        strategies = [
            (By.XPATH, "//button[contains(text(), \"Contacter l'agence\")]"),
            (By.XPATH, "//button[contains(., \"Contacter l'agence\")]"),
            (By.XPATH, "//button[contains(translate(text(),'contacter','CONTACTER'),'CONTACTER')]"),
            (By.XPATH, "//button[contains(., 'Contacter')]"),
            (By.XPATH, "//button[contains(., 'Contact')]"),
            (By.XPATH, "//button[contains(., 'Envoyer')]"),
            (By.XPATH, "//button[contains(., 'Message')]"),
        ]

        for i, (by, sel) in enumerate(strategies, start=1):
            try:
                btn = WebDriverWait(self.driver, 4).until(
                    EC.element_to_be_clickable((by, sel))
                )
                texte = (btn.text or "").strip().replace("\n", " ")
                logger.info(f"Bouton contact trouve (strategie {i}) : '{texte}'")
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn
                )
                fermer_popup_usercentrics(self.driver)
                self.scraper._attendre(0.3, 0.6)
                try:
                    btn.click()
                except (ElementClickInterceptedException, ElementNotInteractableException):
                    self.driver.execute_script("arguments[0].click();", btn)
                logger.info("Clic bouton contact effectue.")

                # Si la page a navigue vers /map — revenir a la fiche
                self.scraper._attendre(0.5, 1.0)
                url_apres = self.driver.current_url
                if "/map" in url_apres and "/map" not in url_avant:
                    logger.warning(f"Navigation inattendue vers /map — retour arriere.")
                    self.driver.back()
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )

                # Attendre le formulaire : firstName OU textarea OU infos personnelles
                try:
                    WebDriverWait(self.driver, 15).until(
                        lambda d: (
                            d.find_elements(By.NAME, "firstName")
                            or d.find_elements(By.NAME, "message")
                            or d.find_elements(By.TAG_NAME, "textarea")
                            or d.find_elements(By.XPATH, "//*[contains(text(),'Informations personnelles')]")
                        )
                    )
                    logger.info("Formulaire detecte apres clic.")
                except TimeoutException:
                    logger.warning("Formulaire non detecte apres 15s.")
                return
            except (TimeoutException, NoSuchElementException,
                    StaleElementReferenceException):
                continue

        logger.warning("Aucun bouton de contact trouve.")

    def _logger_tous_champs(self) -> None:
        """Inventaire complet de tous les inputs : tag, name, id, type,
        placeholder, value, visible, enabled."""
        logger.info("[DEBUG] Inventaire complet des champs :")
        total = 0
        for tag in ["input", "textarea", "select"]:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, tag):
                    try:
                        logger.info(
                            f"  <{tag}> "
                            f"name='{safe_log(el.get_attribute('name') or '')}' | "
                            f"id='{safe_log(el.get_attribute('id') or '')}' | "
                            f"type='{safe_log(el.get_attribute('type') or '')}' | "
                            f"placeholder='{safe_log(el.get_attribute('placeholder') or '')}' | "
                            f"value='{safe_log(el.get_attribute('value') or '')}' | "
                            f"visible={el.is_displayed()} | "
                            f"enabled={el.is_enabled()}"
                        )
                        total += 1
                    except StaleElementReferenceException:
                        continue
            except Exception:
                continue
        logger.info(f"[DEBUG] Total : {total} champ(s)")

    def _debug_avant_remplissage(self) -> None:
        """Log URL, titre et nombre d'occurrences de chaque champ par name."""
        logger.info(f"[DEBUG] URL    : {safe_log(self.driver.current_url)}")
        logger.info(f"[DEBUG] Titre  : {safe_log(self.driver.title)}")
        for name in ["firstName", "lastName", "email", "phoneNumber"]:
            try:
                n = len(self.driver.find_elements(By.NAME, name))
                logger.info(f"[DEBUG] input[name='{name}'] : {n} occurrence(s)")
            except Exception:
                pass

    # ── Detection du formulaire ───────────────────────────────────────────────

    def _detecter_mode_formulaire(self) -> str | None:
        """
        Attend jusqu'a 15s que le formulaire apparaisse, puis detecte le mode.

        Returns:
            "complet"  — champs firstName/lastName/email presents (non connecte)
            "connecte" — utilisateur deja connecte, carte infos personnelles visible
            None       — aucun formulaire detecte apres attente
        """
        # Attente explicite : le formulaire est rendu par JS apres le clic
        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: (
                    d.find_elements(By.NAME, "firstName")
                    or d.find_elements(By.NAME, "message")
                    or d.find_elements(By.TAG_NAME, "textarea")
                    or d.find_elements(By.XPATH, "//*[contains(text(),'Informations personnelles')]")
                    or d.find_elements(By.NAME, "email")
                )
            )
        except TimeoutException:
            pass  # On laisse les checks ci-dessous determiner le mode

        # Mode 1 : formulaire complet (firstName present)
        try:
            el = self.driver.find_element(By.NAME, "firstName")
            if el:
                logger.info("Mode detecte : formulaire complet")
                print("[FORMULAIRE] Mode detecte : formulaire complet")
                return "complet"
        except NoSuchElementException:
            pass

        # Mode 2 : utilisateur connecte
        indicateurs_connecte = [
            (By.XPATH, "//*[contains(text(),'Informations personnelles')]"),
            (By.XPATH, "//*[contains(@aria-label,'modifier') or contains(@aria-label,'Modifier')]"),
            (By.XPATH, "//*[contains(@aria-label,'editer') or contains(@aria-label,'Editer')]"),
            (By.XPATH, "//button[contains(@title,'Modifier')]"),
            (By.XPATH, "//*[contains(@class,'contact') or contains(@class,'Contact')]"
                       "//*[contains(text(),'@')]"),
        ]
        for by, sel in indicateurs_connecte:
            try:
                el = self.driver.find_element(by, sel)
                if el:
                    logger.info("Mode detecte : utilisateur connecte")
                    print("[FORMULAIRE] Mode detecte : utilisateur connecte")
                    return "connecte"
            except NoSuchElementException:
                continue

        # Fallback : textarea ou input message visible
        for by, sel in [
            (By.TAG_NAME,     "textarea"),
            (By.NAME,         "message"),
            (By.CSS_SELECTOR, "textarea[name='message']"),
        ]:
            try:
                el = self.driver.find_element(by, sel)
                if el.is_displayed():
                    logger.info("Mode detecte : textarea/message visible")
                    print("[FORMULAIRE] Mode detecte : textarea visible")
                    return "connecte"
            except NoSuchElementException:
                continue

        logger.warning("Aucun formulaire detecte.")
        print("[FORMULAIRE] Aucun formulaire detecte")
        return None

    def _formulaire_present(self) -> bool:
        """Compatibilite — utilise _detecter_mode_formulaire en interne."""
        return self._detecter_mode_formulaire() is not None

    # ── Remplissage ───────────────────────────────────────────────────────────

    def _sauvegarder_debug_formulaire(self) -> None:
        """Sauvegarde screenshot + HTML avec les noms standardises si le remplissage echoue."""
        try:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            self.driver.save_screenshot(str(SCREENSHOTS_DIR / "debug_formulaire.png"))
            logger.info("Screenshot sauvegarde : screenshots/debug_formulaire.png")
        except Exception:
            pass
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (_DEBUG_DIR / "formulaire.html").write_text(
                self.driver.page_source, encoding="utf-8"
            )
            logger.info("HTML sauvegarde : debug/formulaire.html")
        except Exception:
            pass

    def _remplir_formulaire(self, prix_str: str = "", mode: str = "complet", annonce=None) -> None:
        if mode == "connecte":
            logger.info("Remplissage mode connecte : message uniquement.")
            ok_message = self._remplir_champ_message(prix_str, annonce=annonce)
            if not ok_message:
                logger.warning("Remplissage message echoue (mode connecte).")
                self._sauvegarder_debug_formulaire()
            return

        # Mode complet : tous les champs
        ok_prenom    = self._remplir_champ_prenom()
        ok_nom       = self._remplir_champ_nom()
        ok_email     = self._remplir_champ_email()
        ok_telephone = self._remplir_champ_telephone()
        ok_message   = self._remplir_champ_message(prix_str, annonce=annonce)

        if not (ok_prenom and ok_email):
            logger.warning("Remplissage partiel — sauvegarde debug formulaire.")
            self._sauvegarder_debug_formulaire()

    def _remplir_element(self, el, valeur: str) -> bool:
        """
        Remplit un WebElement avec 3 methodes en cascade.
        Logue l'etat avant et verifie la valeur apres chaque tentative.
        """
        # Log etat avant remplissage
        try:
            logger.info(
                f"  [avant] displayed={el.is_displayed()} | "
                f"enabled={el.is_enabled()} | "
                f"value='{safe_log(el.get_attribute('value') or '')}'"
            )
        except Exception:
            pass

        # Methode 1 : interaction clavier
        try:
            el.click()
            el.send_keys(Keys.CONTROL + "a")
            el.send_keys(Keys.DELETE)
            for lettre in valeur:
                el.send_keys(lettre)
                time.sleep(random.uniform(0.03, 0.08))
            valeur_lue = el.get_attribute("value") or ""
            logger.info(f"  [apres M1] value='{safe_log(valeur_lue)}'")
            if valeur_lue:
                return True
        except Exception:
            pass

        # Methode 2 : JS React natif (setter + events)
        try:
            self.driver.execute_script(_JS_REMPLIR_CHAMP, el, valeur)
            valeur_lue = el.get_attribute("value") or ""
            logger.info(f"  [apres M2 JS React] value='{safe_log(valeur_lue)}'")
            if valeur_lue:
                return True
        except Exception:
            pass

        # Methode 3 : assignation directe JS
        try:
            self.driver.execute_script(
                "arguments[0].value = arguments[1];", el, valeur
            )
            valeur_lue = el.get_attribute("value") or ""
            logger.info(f"  [apres M3 JS direct] value='{safe_log(valeur_lue)}'")
            if valeur_lue:
                return True
        except Exception:
            pass

        logger.warning("  Toutes les methodes de remplissage ont echoue.")
        return False

    def _trouver_et_remplir(
        self, xpath_last: str, libelle: str, valeur: str,
        fallback_selectors: list[tuple] | None = None
    ) -> bool:
        """
        Trouve le DERNIER element correspondant a xpath_last et le remplit.
        Attend jusqu'a 15s. Screenshot + HTML si introuvable.
        """
        # Priorite : dernier champ (formulaire contact, pas profil)
        try:
            el = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.XPATH, xpath_last))
            )
            WebDriverWait(self.driver, 5).until(EC.visibility_of(el))
            if self._remplir_element(el, valeur):
                logger.info(f"Champ {libelle} trouve et rempli.")
                return True
        except (TimeoutException, Exception):
            pass

        # Fallback : autres selecteurs
        if fallback_selectors:
            for by, sel in fallback_selectors:
                try:
                    el = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((by, sel))
                    )
                    if self._remplir_element(el, valeur):
                        logger.info(f"Champ {libelle} rempli (fallback).")
                        return True
                except Exception:
                    continue

        logger.warning(f"Champ {libelle} introuvable — screenshot.")
        capturer_erreur(self.driver, "champs_introuvables")
        return False

    def _remplir_champ_prenom(self) -> bool:
        return self._trouver_et_remplir(
            xpath_last="(//input[@name='firstName'])[last()]",
            libelle="prenom",
            valeur=self.prenom,
        )

    def _remplir_champ_nom(self) -> bool:
        return self._trouver_et_remplir(
            xpath_last="(//input[@name='lastName'])[last()]",
            libelle="nom",
            valeur=self.nom,
        )

    def _remplir_champ_email(self) -> bool:
        return self._trouver_et_remplir(
            xpath_last="(//input[@name='email'])[last()]",
            libelle="email",
            valeur=self.email,
            fallback_selectors=[
                (By.CSS_SELECTOR, "input[type='email']"),
            ],
        )

    def _remplir_champ_telephone(self) -> bool:
        """
        Strategies stables (pas d'IDs React ni de classes css-xxxxx) :
          1. input[type='tel']  — attribut type stable
          2. input[name='phoneNumber'] visible
          3. input visible precedant le hidden phoneNumber
          4. JS React natif sur le hidden phoneNumber
          5. input texte visible sans name (dernier fallback)
        """
        strategies = [
            # Strategie 1 : type='tel' (attribut HTML stable)
            ("tel", lambda: self._trouver_input_et_remplir(
                By.CSS_SELECTOR, "input[type='tel']", "telephone (type=tel)"
            )),
            # Strategie 2 : name='phoneNumber' visible
            ("phoneNumber visible", lambda: self._trouver_input_et_remplir(
                By.XPATH,
                "(//input[@name='phoneNumber' and not(@type='hidden')])[last()]",
                "telephone (name=phoneNumber visible)"
            )),
            # Strategie 3 : input visible precedant le hidden phoneNumber
            ("precedant hidden", lambda: self._trouver_input_et_remplir(
                By.XPATH,
                "//input[@name='phoneNumber']/preceding-sibling::input[not(@type='hidden')][last()]",
                "telephone (precedant hidden)"
            )),
            # Strategie 4 : JS React sur hidden phoneNumber
            ("hidden JS", lambda: self._remplir_hidden_telephone()),
            # Strategie 5 : input texte visible sans name
            ("sans name", lambda: self._remplir_input_texte_sans_name()),
        ]

        for nom, fn in strategies:
            try:
                if fn():
                    return True
            except Exception:
                continue

        logger.warning("Champ telephone introuvable.")
        return False

    def _trouver_input_et_remplir(self, by, sel: str, libelle: str) -> bool:
        el = WebDriverWait(self.driver, 4).until(
            EC.presence_of_element_located((by, sel))
        )
        if el.is_displayed() and el.is_enabled():
            if self._remplir_element(el, self.telephone):
                logger.info(f"Champ telephone rempli ({libelle}).")
                return True
        return False

    def _remplir_hidden_telephone(self) -> bool:
        hidden = self.driver.find_element(
            By.XPATH, "(//input[@name='phoneNumber'])[last()]"
        )
        self.driver.execute_script(_JS_REMPLIR_CHAMP, hidden, self.telephone)
        logger.info("Champ telephone rempli (hidden JS).")
        return True

    def _remplir_input_texte_sans_name(self) -> bool:
        inputs = self.driver.find_elements(
            By.XPATH,
            "//input[@type='text' and not(@name) and not(@type='hidden')]"
        )
        for el in inputs:
            try:
                if el.is_displayed() and el.is_enabled():
                    if self._remplir_element(el, self.telephone):
                        logger.info("Champ telephone rempli (input sans name).")
                        return True
            except Exception:
                continue
        return False

    def _cliquer_ajouter_message(self) -> bool:
        """
        Trouve et clique sur le div 'Ajouter un message' pour
        faire apparaitre le textarea.
        """
        logger.info("Recherche du lien Ajouter un message...")

        # Strategies stables — pas d'IDs React ni de classes css-xxxxx
        selecteurs = [
            (By.XPATH, "//*[contains(.,'Ajouter un message')]"),
            (By.XPATH, "//div[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//span[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//button[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//a[contains(text(),'Ajouter un message')]"),
        ]

        element = None
        for by, sel in selecteurs:
            try:
                element = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((by, sel))
                )
                logger.info(f"Lien Ajouter un message trouve (tag={element.tag_name})")
                break
            except (TimeoutException, NoSuchElementException):
                continue

        if element is None:
            logger.warning("Lien 'Ajouter un message' introuvable.")
            logger.info("[DEBUG] Divs contenant 'message' :")
            try:
                candidats = self.driver.find_elements(
                    By.XPATH,
                    "//div[contains(translate(text(),'MESSAGE','message'),'message')]"
                )
                for el in candidats[:20]:
                    try:
                        logger.info(
                            f"  tag={el.tag_name} | "
                            f"texte='{(el.text or '').strip()[:80]}' | "
                            f"class='{el.get_attribute('class') or ''}'"
                        )
                    except StaleElementReferenceException:
                        continue
            except Exception as exc:
                logger.warning(f"[DEBUG] Erreur listing divs : {exc}")
            return False

        # Scroll puis clic JS (pas de click() Selenium)
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", element
        )
        self.scraper._attendre(0.5, 0.5)
        fermer_popup_usercentrics(self.driver)
        self.driver.execute_script("arguments[0].click();", element)
        logger.info("Clic effectue")

        # Attendre le textarea
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.TAG_NAME, "textarea"))
            )
            logger.info("Textarea detecte")
            return True
        except TimeoutException:
            logger.warning("Textarea introuvable")
            return False

    def _remplir_champ_message(self, prix_str: str = "", annonce=None) -> bool:
        """
        Flux correct SeLoger :
          1. Cliquer "Ajouter un message" pour faire apparaitre le textarea
          2. Remplir le textarea
          3. "Contacter l'agence" est clique par _cliquer_envoyer() apres
        """
        message = _generer_message(prix_str)
        # Stocker le message dans l'annonce pour l'Excel
        if annonce is not None:
            annonce.message_envoye = message
        # Afficher le prix proposé dans la console
        m_offre = re.search(r"offre d'achat a ([\d\s]+\u20ac)", message)
        if m_offre:
            logger.info(f"Prix propose : {m_offre.group(1)}")
            print(f"[OFFRE] Prix propose : {m_offre.group(1)}")
        else:
            logger.info("Message sans offre de prix (prix inconnu)")
            print("[OFFRE] Message sans offre de prix")
        logger.info(f"Message genere : {message[:60]}...")

        # ── Etape 1 : cliquer "Ajouter un message" ────────────────────────────
        logger.info("Recherche du lien 'Ajouter un message'...")
        selecteurs_ajouter = [
            (By.XPATH, "//*[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//button[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//div[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//span[contains(text(),'Ajouter un message')]"),
            (By.XPATH, "//a[contains(text(),'Ajouter un message')]"),
        ]
        clique = False
        for by, sel in selecteurs_ajouter:
            try:
                el = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((by, sel))
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                fermer_popup_usercentrics(self.driver)
                self.driver.execute_script("arguments[0].click();", el)
                logger.info(f"Clic 'Ajouter un message' effectue ({sel})")
                print("[MESSAGE] Clic 'Ajouter un message'")
                clique = True
                break
            except (TimeoutException, NoSuchElementException):
                continue

        if not clique:
            logger.warning("Lien 'Ajouter un message' introuvable.")
            print("[MESSAGE] 'Ajouter un message' introuvable")

        # ── Etape 2 : attendre et trouver le textarea ─────────────────────────
        textarea = None
        for by, sel in [
            (By.NAME,         "message"),
            (By.CSS_SELECTOR, "textarea[name='message']"),
            (By.TAG_NAME,     "textarea"),
        ]:
            try:
                textarea = WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((by, sel))
                )
                logger.info(f"Textarea trouve ({sel})")
                print(f"[MESSAGE] Textarea trouve ({sel})")
                break
            except (TimeoutException, NoSuchElementException):
                continue

        if textarea is None:
            logger.warning("Textarea introuvable apres clic 'Ajouter un message'.")
            print("[MESSAGE] Echec : textarea introuvable")
            self._sauvegarder_debug_formulaire()
            return False

        # ── Etape 3 : remplir le textarea ─────────────────────────────────────
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", textarea
            )
            textarea.click()
            textarea.clear()
            textarea.send_keys(message)
            valeur = textarea.get_attribute("value") or ""
            if valeur:
                logger.info("Message rempli avec succes.")
                print("[MESSAGE] Message rempli avec succes")
                return True
            else:
                logger.warning("Echec remplissage : valeur vide apres send_keys.")
                print("[MESSAGE] Echec du remplissage")
                return False
        except Exception as exc:
            logger.warning(f"Erreur remplissage textarea : {exc}")
            print(f"[MESSAGE] Erreur remplissage : {exc}")
            return False

    # ── Envoi ─────────────────────────────────────────────────────────────────

    def _cliquer_envoyer(self) -> None:
        """Clique sur le bouton d'envoi — 'Contacter l'agence' en priorite."""
        selectors_btn = [
            (By.XPATH, "//button[contains(text(), \"Contacter l'agence\")]"),
            (By.XPATH, "//button[contains(., \"Contacter l'agence\")]"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(text(), 'Envoyer')]"),
            (By.XPATH, "//button[contains(text(), 'Envoyer ma demande')]"),
            (By.XPATH, "//button[contains(text(), 'Envoyer un message')]"),
            (By.CSS_SELECTOR, "input[type='submit']"),
        ]

        for by, sel in selectors_btn:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((by, sel))
                )
            except (TimeoutException, NoSuchElementException):
                continue

            fermer_popup_usercentrics(self.driver)
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn
            )

            try:
                logger.info("Tentative clic Selenium")
                btn.click()
                self.scraper._attendre(1.0, 2.0)
                logger.info("Bouton Envoyer clique (Selenium).")
                return

            except ElementClickInterceptedException:
                logger.warning("Clic bloque, tentative JS")
                try:
                    self.driver.save_screenshot(
                        str(SCREENSHOTS_DIR / "cookies_blocking.png")
                    )
                except Exception:
                    pass
                fermer_popup_usercentrics(self.driver)
                try:
                    self.driver.execute_script("arguments[0].click();", btn)
                    self.scraper._attendre(1.0, 2.0)
                    logger.info("Clic JS reussi")
                    return
                except Exception as exc_js:
                    logger.warning(f"Clic JS echoue : {exc_js}")
                    continue

            except Exception:
                continue

        logger.warning("Bouton Envoyer introuvable.")
