"""
logger.py — Configuration du systeme de logs.

Produit deux sorties :
  - Console  : niveau INFO, format colore
  - Fichier  : niveau DEBUG, rotation quotidienne dans logs/bot_seloger.log

Corrections Windows :
  - SafeTimedRotatingFileHandler capture les PermissionError lors du rollover
    (WinError 32 : fichier verrouille par un autre process).
  - delay=True : le fichier n'est ouvert qu'a la premiere ecriture.
  - emit() ne laisse jamais remonter d'exception : le logging ne crashe jamais.
  - Protection renforcee contre les handlers dupliques.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config import LOGS_DIR


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    TimedRotatingFileHandler immune aux PermissionError Windows (WinError 32).

    Sur Windows, le rollover tente de renommer le fichier log alors qu'il est
    encore ouvert par un autre handler ou process. Cette sous-classe :
      - capture silencieusement l'erreur dans doRollover()
      - capture toute exception dans emit() pour ne jamais interrompre le programme
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # Fichier verrouille par Windows : on reporte le rollover silencieusement
            pass
        except Exception:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:
            # Le logging ne doit jamais faire planter le programme
            self.handleError(record)


def setup_logger(name: str = "bot_seloger") -> logging.Logger:
    """
    Cree et retourne un logger nomme avec handler console + fichier.

    Garanties :
      - Jamais de handlers dupliques (verification par nom de classe + fichier).
      - Jamais d'exception levee depuis le systeme de log.

    Args:
        name: Nom du logger (par defaut 'bot_seloger').

    Returns:
        Logger configure pret a l'emploi.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "bot_seloger.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Verification renforcee contre les handlers dupliques :
    # on regarde si un file handler sur ce meme fichier est deja installe.
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                if Path(h.baseFilename).resolve() == log_file.resolve():
                    return logger
            except Exception:
                return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Handler console ──────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # ── Handler fichier (rotation quotidienne, 7 jours conserves) ────────────
    # delay=True : le fichier n'est ouvert qu'a la premiere ecriture
    #              → evite le verrouillage precoce qui cause WinError 32
    file_handler = SafeTimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
