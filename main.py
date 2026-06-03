"""
main.py — Point d'entrée du bot SeLoger.

Flux optimisé en 4 phases chronométrées :
  1. Recherche   — Selenium résout la ville et génère l'URL.
  2. Collecte    — Selenium récupère tous les liens /annonces/ sur toutes les pages.
  3. Extraction  — requests + BeautifulSoup en parallèle (ThreadPoolExecutor).
  4. Contact     — Selenium ouvre chaque fiche, remplit et envoie le formulaire.
"""

import sys
import time as _time

# Fix encodage Unicode Windows (evite les erreurs charmap sur les logs)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def safe_log(text) -> str:
    """Convertit n'importe quel texte en str sans jamais lever d'exception."""
    try:
        return str(text)
    except Exception:
        return repr(text)

from config import (
    DEFAULT_EMAIL,
    DEFAULT_NOM,
    DEFAULT_PRENOM,
    DEFAULT_TELEPHONE,
    MESSAGE_CONTACT,
)
from contact_manager import ContactManager
from excel_manager import ExcelManager
from fetcher import extraire_annonces_parallele
from logger import setup_logger
from scraper import Annonce, SeLogerScraper
from url_builder import generer_url_seloger

logger = setup_logger("main")


def rechercher_annonces(
    ville: str,
    transaction: str,
    type_logement: str,
    budget_max: int,
    surface_min: int,
    pieces_min: int,
    chambres_min: int,
    prenom: str = DEFAULT_PRENOM,
    nom: str = DEFAULT_NOM,
    email: str = DEFAULT_EMAIL,
    telephone: str = DEFAULT_TELEPHONE,
    envoyer_message: bool = True,
    nombre_messages: int = 0,
) -> None:
    """
    Lance la recherche automatisée d'annonces sur SeLoger et contacte chacune.

    Args:
        ville:            Nom de la ville (ex. : "Marseille").
        transaction:      "achat" ou "location".
        type_logement:    "appartement", "maison" ou "appartement,maison".
        budget_max:       Prix maximum en euros.
        surface_min:      Surface minimale en m².
        pieces_min:       Nombre minimal de pièces.
        chambres_min:     Nombre minimal de chambres.
        prenom:           Prénom à utiliser dans le formulaire.
        nom:              Nom à utiliser dans le formulaire.
        email:            Email à utiliser dans le formulaire.
        telephone:        Téléphone à utiliser dans le formulaire.
        envoyer_message:  True → envoie réellement, False → simulation.
        nombre_messages:  Nombre maximum de messages à envoyer (0 = illimité).
    """
    t_total_debut = _time.time()

    logger.info("=" * 60)
    logger.info("Démarrage du bot SeLoger")
    logger.info(
        f"Critères : {ville} | {transaction} | {type_logement} | "
        f"<={budget_max}EUR | >={surface_min}m2 | >={pieces_min}p | >={chambres_min}ch"
    )
    limite = str(nombre_messages) if nombre_messages > 0 else "illimite"
    logger.info(f"Messages a envoyer : {limite}")
    logger.info("=" * 60)

    # ── Initialiser Excel ─────────────────────────────────────────────────────
    excel = ExcelManager()
    liens_deja_contactes = excel.charger_liens_deja_contactes()
    logger.info(f"{len(liens_deja_contactes)} annonce(s) deja contactee(s) en base.")

    with SeLogerScraper() as scraper:

        # ── Phase 1 : Recherche ───────────────────────────────────────────────
        t0 = _time.time()
        try:
            url = generer_url_seloger(
                ville=ville,
                transaction=transaction,
                type_logement=type_logement,
                budget_max=budget_max,
                surface_min=surface_min,
                pieces_min=pieces_min,
                chambres_min=chambres_min,
                driver=scraper.driver,
            )
        except (ValueError, ConnectionError) as exc:
            logger.error(f"Impossible de generer l'URL : {exc}")
            return
        t_recherche = _time.time() - t0
        logger.info(f"Temps recherche : {t_recherche:.1f}s")

        # ── Phase 2 : Collecte des liens ──────────────────────────────────────
        t0 = _time.time()
        try:
            tous_liens = scraper.collecter_liens(url)
        except Exception as exc:
            logger.error(f"Erreur lors de la collecte des liens : {exc}")
            return
        t_collecte = _time.time() - t0
        logger.info(f"Temps recuperation annonces : {t_collecte:.1f}s")
        logger.info(f"Annonces trouvees : {len(tous_liens)}")

        if not tous_liens:
            logger.warning("Aucun lien collecte. Fin du programme.")
            _afficher_rapport(0, 0, 0, 0, _time.time() - t_total_debut)
            return

        # Filtrer les liens déjà contactés avant l'extraction (gain de temps)
        liens_nouveaux = [l for l in tous_liens if l not in liens_deja_contactes]
        logger.info(
            f"{len(liens_nouveaux)} nouveaux lien(s) a analyser "
            f"({len(tous_liens) - len(liens_nouveaux)} doublon(s) ignores)."
        )

        if nombre_messages > 0:
            liens_nouveaux = liens_nouveaux[:nombre_messages]
            logger.info(f"Limite appliquee : {len(liens_nouveaux)} annonce(s).")

        # Exporter les cookies Selenium pour requests (anti-blocage)
        cookies = scraper.exporter_cookies()

        # ── Phase 3 : Extraction (requests + BS4 en parallèle) ────────────────
        t0 = _time.time()
        annonces = extraire_annonces_parallele(liens_nouveaux, cookies=cookies)
        t_extraction = _time.time() - t0
        logger.info(f"Temps extraction : {t_extraction:.1f}s")
        logger.info(f"Annonces analysees : {len(annonces)}")

        if not annonces:
            logger.warning("Aucune annonce extraite.")
            _afficher_rapport(len(tous_liens), 0, 0, 0, _time.time() - t_total_debut)
            return

        # ── Phase 4 : Contact (Selenium — formulaires) ────────────────────────
        t0 = _time.time()
        contact_mgr = ContactManager(
            scraper=scraper,
            prenom=prenom,
            nom=nom,
            email=email,
            telephone=telephone,
        )
        compteurs = {"contacte": 0, "doublon": 0, "erreur": 0}
        erreurs_log: list[dict] = []

        for i, annonce in enumerate(annonces, start=1):
            if nombre_messages > 0 and compteurs["contacte"] >= nombre_messages:
                logger.info(f"Limite de {nombre_messages} message(s) atteinte.")
                break

            logger.info(f"[{i}/{len(annonces)}] {annonce.titre or annonce.lien}")

            # Anti-doublon (vérification finale)
            if annonce.lien in liens_deja_contactes:
                logger.info("  Doublon ignore.")
                compteurs["doublon"] += 1
                excel.enregistrer_annonce(
                    titre=annonce.titre, prix=annonce.prix,
                    surface=annonce.surface, pieces=annonce.pieces,
                    ville=annonce.localisation or ville, lien=annonce.lien,
                    agence=annonce.agence or annonce.proprietaire,
                    message_envoye="", statut="Doublon",
                )
                continue

            # Le bot ne s'arrete jamais sur une annonce — toutes les exceptions
            # sont capturees et l'annonce suivante est traitee
            try:
                succes, statut = contact_mgr.contacter_annonce(
                    annonce, envoyer_message=envoyer_message
                )
            except Exception as exc:
                logger.error(f"Erreur inattendue sur {annonce.lien} : {exc}")
                succes, statut = False, f"Erreur : {type(exc).__name__}"

            message_enregistre = MESSAGE_CONTACT if succes and envoyer_message else ""
            excel.enregistrer_annonce(
                titre=annonce.titre, prix=annonce.prix,
                surface=annonce.surface, pieces=annonce.pieces,
                ville=annonce.localisation or ville, lien=annonce.lien,
                agence=annonce.agence or annonce.proprietaire,
                message_envoye=message_enregistre, statut=statut,
            )

            if succes:
                liens_deja_contactes.add(annonce.lien)
                compteurs["contacte"] += 1
            else:
                compteurs["erreur"] += 1
                erreurs_log.append({"lien": annonce.lien, "statut": statut})

            scraper._attendre()

        t_envoi = _time.time() - t0
        logger.info(f"Temps envoi messages : {t_envoi:.1f}s")

    # ── Rapport final ─────────────────────────────────────────────────────────
    t_total = _time.time() - t_total_debut
    _afficher_rapport(
        len(tous_liens),
        len(annonces),
        compteurs["contacte"],
        compteurs["erreur"],
        t_total,
        t_recherche=t_recherche,
        t_collecte=t_collecte,
        t_extraction=t_extraction,
        t_envoi=t_envoi,
        erreurs_log=erreurs_log,
    )


def _afficher_rapport(
    nb_trouvees: int,
    nb_analysees: int,
    nb_envoyes: int,
    nb_echouees: int,
    t_total: float,
    t_recherche: float = 0.0,
    t_collecte:  float = 0.0,
    t_extraction: float = 0.0,
    t_envoi:      float = 0.0,
    erreurs_log:  list | None = None,
) -> None:
    """Affiche le rapport de performance final."""
    logger.info("=" * 60)
    logger.info("RAPPORT FINAL")
    logger.info(f"  Annonces trouvees   : {nb_trouvees}")
    logger.info(f"  Annonces analysees  : {nb_analysees}")
    logger.info(f"  Annonces contactees : {nb_envoyes}")
    logger.info(f"  Annonces echouees   : {nb_echouees}")
    logger.info("  ---")
    logger.info(f"  Temps recherche              : {t_recherche:.1f}s")
    logger.info(f"  Temps recuperation annonces  : {t_collecte:.1f}s")
    logger.info(f"  Temps extraction             : {t_extraction:.1f}s")
    logger.info(f"  Temps envoi messages         : {t_envoi:.1f}s")
    logger.info(f"  Temps total                  : {t_total:.1f}s")
    if erreurs_log:
        logger.info("  ---")
        logger.info(f"  Erreurs ({len(erreurs_log)}) :")
        for e in erreurs_log:
            logger.info(f"    [{e['statut']}] {e['lien']}")
    logger.info("=" * 60)


# ── Lancement direct ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    rechercher_annonces(
        ville="Marseille",
        transaction="location",
        type_logement="appartement",
        budget_max=1_600,
        surface_min=80,
        pieces_min=4,
        chambres_min=2,
        prenom="Raphael",
        nom="Tepe",
        email="raph.tepe@gmail.com",
        telephone="0671635640",
        envoyer_message=False,   # mettre True pour envoyer réellement
        nombre_messages=10,
    )
