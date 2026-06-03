"""
logger.py — Configuration du système de logs.

Produit deux sorties :
  - Console  : niveau INFO, format coloré
  - Fichier  : niveau DEBUG, rotation quotidienne dans logs/bot_seloger.log
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config import LOGS_DIR


def setup_logger(name: str = "bot_seloger") -> logging.Logger:
    """
    Crée et retourne un logger nommé avec handler console + fichier.

    Args:
        name: Nom du logger (par défaut 'bot_seloger').

    Returns:
        Logger configuré prêt à l'emploi.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "bot_seloger.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Éviter les handlers dupliqués lors de réimportations
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Handler console ──────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # ── Handler fichier (rotation quotidienne, 7 jours conservés) ────────────
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
