"""
fetcher.py — Extraction des données des fiches annonces SeLoger.

Utilise requests.Session + BeautifulSoup au lieu de Selenium pour lire
les pages de détail : 5 à 10× plus rapide, aucune instance Chrome nécessaire.

Traitement parallèle via ThreadPoolExecutor (4 à 8 workers simultanés).

Cache en mémoire : chaque URL n'est traitée qu'une seule fois par session.

Stratégies d'extraction (dans l'ordre) :
  1. JSON-LD (structured data) — données propres si présentes
  2. Balises Open Graph / meta — rapide et souvent rempli
  3. Sélecteurs CSS BeautifulSoup — fallback générique
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import MAX_WORKERS
from logger import setup_logger
from scraper import Annonce

logger = setup_logger("fetcher")

# Headers navigateur réaliste pour éviter les blocages
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.seloger.com/",
}


def extraire_annonces_parallele(
    liens: list[str],
    cookies: dict[str, str] | None = None,
    max_workers: int = MAX_WORKERS,
) -> list[Annonce]:
    """
    Extrait les données de plusieurs fiches en parallèle.

    Args:
        liens:       Liste de hrefs d'annonces SeLoger.
        cookies:     Cookies exportés depuis Selenium (session partagée).
        max_workers: Nombre de threads simultanés (défaut : config.MAX_WORKERS).

    Returns:
        Liste d'objets Annonce remplis (les échecs sont ignorés).
    """
    if not liens:
        return []

    logger.info(
        f"Extraction de {len(liens)} fiche(s) — {max_workers} workers parallèles."
    )

    # Cache en mémoire — évite de retraiter un même lien deux fois
    cache: set[str] = set()
    cache_lock = Lock()

    annonces: list[Annonce] = []
    annonces_lock = Lock()

    # Session partagée entre tous les threads (connexions HTTP réutilisées)
    session = requests.Session()
    session.headers.update(_HEADERS)
    if cookies:
        session.cookies.update(cookies)

    def _traiter(href: str) -> Optional[Annonce]:
        with cache_lock:
            if href in cache:
                return None
            cache.add(href)
        return _extraire_annonce(href, session)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_traiter, href): href for href in liens}
        for future in as_completed(futures):
            try:
                annonce = future.result()
                if annonce:
                    with annonces_lock:
                        annonces.append(annonce)
            except Exception as exc:
                logger.warning(f"Erreur worker : {exc}")

    logger.info(f"Extraction terminée : {len(annonces)}/{len(liens)} réussies.")
    return annonces


def _extraire_annonce(href: str, session: requests.Session) -> Optional[Annonce]:
    """
    Télécharge et parse une fiche annonce avec requests + BeautifulSoup.

    Args:
        href:    URL complète de l'annonce.
        session: Session requests partagée.

    Returns:
        Annonce remplie, ou None si la récupération échoue.
    """
    try:
        resp = session.get(href, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"Erreur HTTP pour {href} : {exc}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    annonce = Annonce(lien=href)

    # ── Stratégie 0 : URL — type_bien et transaction toujours présents ────────
    # Pattern : /annonces/(achat|location)/(appartement|maison|...)/(ville)/
    m_url = re.search(r'/annonces/(achat|location)/([^/]+)/', href, re.I)
    if m_url:
        tx_raw = m_url.group(1).lower()
        type_raw = m_url.group(2).lower()
        annonce.transaction = "Achat" if tx_raw == "achat" else "Location"
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

    # ── Stratégie 1 : JSON-LD ─────────────────────────────────────────────────
    _extraire_jsonld(soup, annonce)

    # ── Stratégie 2 : meta Open Graph ─────────────────────────────────────────
    if not annonce.titre:
        _extraire_meta(soup, annonce)

    # ── Stratégie 3 : sélecteurs CSS ─────────────────────────────────────────
    _extraire_css(soup, annonce)

    logger.info(
        f"  OK : '{annonce.titre[:50]}' | {annonce.prix} | "
        f"{annonce.surface} | {annonce.localisation}"
    )
    return annonce


# ── Stratégie 1 : JSON-LD ─────────────────────────────────────────────────────

def _extraire_jsonld(soup: BeautifulSoup, annonce: Annonce) -> None:
    """
    Extrait les données depuis les balises <script type='application/ld+json'>.
    SeLoger inclut souvent des données structurées Schema.org (RealEstateListing).
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            schemas = data if isinstance(data, list) else [data]

            for schema in schemas:
                t = schema.get("@type", "")
                if t not in ("RealEstateListing", "Offer", "Product", "ItemPage",
                             "Apartment", "House", "Accommodation", ""):
                    pass  # essayer quand même

                if not annonce.titre:
                    annonce.titre = (
                        schema.get("name")
                        or schema.get("headline")
                        or ""
                    )

                if not annonce.prix:
                    offers = schema.get("offers", {})
                    if isinstance(offers, dict):
                        prix = offers.get("price") or offers.get("lowPrice")
                        devise = offers.get("priceCurrency", "EUR")
                        if prix:
                            annonce.prix = f"{prix} {devise}"
                    annonce.prix = annonce.prix or str(schema.get("price", ""))

                if not annonce.surface:
                    annonce.surface = str(
                        schema.get("floorSize", {}).get("value", "")
                        or schema.get("floorSize", "")
                    )

                if not annonce.localisation:
                    addr = schema.get("address", {})
                    if isinstance(addr, dict):
                        annonce.localisation = (
                            addr.get("addressLocality")
                            or addr.get("streetAddress")
                            or ""
                        )
                    elif isinstance(addr, str):
                        annonce.localisation = addr

                if not annonce.agence:
                    seller = schema.get("seller", {})
                    if isinstance(seller, dict):
                        annonce.agence = seller.get("name", "")

        except (json.JSONDecodeError, AttributeError):
            continue


# ── Stratégie 2 : meta Open Graph ─────────────────────────────────────────────

def _extraire_meta(soup: BeautifulSoup, annonce: Annonce) -> None:
    """Extrait titre, description et image depuis les balises meta."""

    def _meta(prop: str) -> str:
        tag = (
            soup.find("meta", property=prop)
            or soup.find("meta", attrs={"name": prop})
        )
        return (tag.get("content") or "") if tag else ""

    if not annonce.titre:
        annonce.titre = _meta("og:title") or (soup.title.string if soup.title else "")

    if not annonce.localisation:
        # Tenter d'extraire la ville depuis le titre ou l'URL
        titre = annonce.titre or ""
        match = re.search(r"à\s+([\w\s\-]+?)(?:\s*[\|\-]|$)", titre)
        if match:
            annonce.localisation = match.group(1).strip()


# ── Stratégie 3 : sélecteurs CSS BeautifulSoup ────────────────────────────────

def _extraire_css(soup: BeautifulSoup, annonce: Annonce) -> None:
    """Complète les champs manquants via des sélecteurs CSS classiques."""

    def _premier_texte(selectors: list[str]) -> str:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
                if el:
                    return el.get_text(strip=True)
            except Exception:
                continue
        return ""

    if not annonce.titre:
        annonce.titre = _premier_texte([
            "h1",
            "[class*='title']",
            "[data-testid='classified-title']",
        ])

    if not annonce.prix:
        annonce.prix = _premier_texte([
            "[data-testid='price']",
            "[class*='price']",
            "[class*='Price']",
            "[class*='prix']",
        ])

    if not annonce.surface:
        annonce.surface = _premier_texte([
            "[data-testid='surface']",
            "[class*='surface']",
            "[class*='Surface']",
        ])

    if not annonce.pieces:
        annonce.pieces = _premier_texte([
            "[data-testid='rooms']",
            "[class*='room']",
            "[class*='piece']",
        ])

    if not annonce.localisation:
        annonce.localisation = _premier_texte([
            "[data-testid='city']",
            "[class*='city']",
            "[class*='location']",
            "[class*='adresse']",
        ])

    if not annonce.agence:
        annonce.agence = _premier_texte([
            "[data-testid='agency-name']",
            "[class*='agency']",
            "[class*='agence']",
        ])

    # Étage
    if not annonce.etage:
        from contact_manager import _extraire_etage
        texte = soup.get_text(" ", strip=True)
        annonce.etage = _extraire_etage(soup, texte)

    # DPE et GES — data-testid='cdp-preview-scale-highlighted'
    # Premier bloc = DPE, second bloc = GES (structure SeLoger stable)
    dpe_ges_els = soup.find_all(attrs={"data-testid": "cdp-preview-scale-highlighted"})
    if len(dpe_ges_els) >= 1 and not annonce.dpe:
        lettre = dpe_ges_els[0].get_text(strip=True).upper()
        if re.match(r'^[A-G]$', lettre):
            annonce.dpe = lettre
    if len(dpe_ges_els) >= 2 and not annonce.ges:
        lettre = dpe_ges_els[1].get_text(strip=True).upper()
        if re.match(r'^[A-G]$', lettre):
            annonce.ges = lettre
    # Fallback regex
    if not annonce.dpe:
        texte = soup.get_text(" ", strip=True)
        m = re.search(r'DPE\s+Classe\s+([A-G])', texte, re.I)
        if not m:
            m = re.search(r'Diagnostic de performance[^A-G]{0,200}([A-G])\b', texte, re.I | re.S)
        if m:
            annonce.dpe = m.group(1).upper()
    if not annonce.ges:
        texte = soup.get_text(" ", strip=True) if not annonce.dpe else texte
        m = re.search(r'GES\s+Classe\s+([A-G])', texte, re.I)
        if not m:
            m = re.search(r"[Ii]ndice d.émission[^A-G]{0,200}([A-G])\b", texte, re.I | re.S)
        if m:
            annonce.ges = m.group(1).upper()
