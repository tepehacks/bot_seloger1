"""
excel_manager.py — Gestion du fichier Excel des annonces contactées.

Responsabilités :
  - Créer le fichier Excel s'il n'existe pas.
  - Charger les liens déjà contactés (anti-doublon).
  - Ajouter une nouvelle ligne après chaque contact.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from config import EXCEL_FILE, EXCEL_COLUMNS
from logger import setup_logger

logger = setup_logger("excel_manager")


class ExcelManager:
    """
    Gestionnaire du fichier Excel de suivi des annonces.

    Attributes:
        path: Chemin vers le fichier Excel.
    """

    def __init__(self, path: Path = EXCEL_FILE) -> None:
        self.path = path
        self._initialiser()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _initialiser(self) -> None:
        """Crée le fichier Excel avec en-têtes si inexistant."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            logger.info(f"Création du fichier Excel : {self.path}")
            wb = Workbook()
            ws = wb.active
            ws.title = "Annonces contactées"
            self._ecrire_entetes(ws)
            wb.save(self.path)
        else:
            logger.debug(f"Fichier Excel existant chargé : {self.path}")

    def _ecrire_entetes(self, ws) -> None:
        """Écrit la ligne d'en-têtes avec mise en forme."""
        ws.append(EXCEL_COLUMNS)

        # Style en-têtes
        header_fill = PatternFill(
            start_color="1F4E79", end_color="1F4E79", fill_type="solid"
        )
        header_font = Font(color="FFFFFF", bold=True)

        for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
            # Largeur automatique approximative
            ws.column_dimensions[get_column_letter(col_idx)].width = max(
                len(col_name) + 4, 15
            )

    # ── Lecture ───────────────────────────────────────────────────────────────

    def charger_liens_deja_contactes(self) -> set[str]:
        """
        Charge l'ensemble des liens déjà présents dans Excel.

        Returns:
            Ensemble de chaînes URL déjà enregistrées.
        """
        if not self.path.exists():
            return set()

        try:
            wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
            ws = wb.active

            col_lien = EXCEL_COLUMNS.index("Lien") + 1  # index Excel (1-based)
            liens: set[str] = set()

            for row in ws.iter_rows(min_row=2, values_only=True):
                lien = row[col_lien - 1]
                if lien:
                    liens.add(str(lien).strip())

            wb.close()
            logger.debug(f"{len(liens)} lien(s) déjà enregistré(s).")
            return liens

        except Exception as exc:
            logger.error(f"Erreur lecture Excel : {exc}")
            return set()

    # ── Écriture ──────────────────────────────────────────────────────────────

    def enregistrer_annonce(
        self,
        titre: str,
        prix: str,
        surface: str,
        pieces: str,
        ville: str,
        lien: str,
        agence: str,
        message_envoye: str,
        statut: str,
    ) -> None:
        """
        Ajoute une ligne dans le fichier Excel.

        Args:
            titre:           Titre de l'annonce.
            prix:            Prix affiché.
            surface:         Surface en m².
            pieces:          Nombre de pièces.
            ville:           Ville / localisation.
            lien:            URL de l'annonce.
            agence:          Nom de l'agence ou du propriétaire.
            message_envoye:  Texte du message envoyé (ou vide).
            statut:          "Contacté", "Erreur", "Doublon", etc.
        """
        try:
            wb = openpyxl.load_workbook(self.path)
            ws = wb.active

            maintenant = datetime.now()
            ligne = [
                maintenant.strftime("%Y-%m-%d"),  # Date
                titre,
                prix,
                surface,
                pieces,
                ville,
                lien,
                agence,
                message_envoye,
                statut,
                maintenant.strftime("%Y-%m-%d %H:%M:%S"),  # Date d'envoi
            ]
            ws.append(ligne)
            wb.save(self.path)
            logger.debug(f"Annonce enregistrée dans Excel : {titre}")

        except Exception as exc:
            logger.error(f"Impossible d'enregistrer dans Excel : {exc}")
