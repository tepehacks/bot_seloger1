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
        """Écrit la ligne d'en-têtes avec mise en forme complète."""
        ws.append(EXCEL_COLUMNS)

        header_fill = PatternFill(
            start_color="1F4E79", end_color="1F4E79", fill_type="solid"
        )
        header_font = Font(color="FFFFFF", bold=True)

        # Largeurs fixes pour colonnes larges
        largeurs_fixes = {
            "URL":            30,
            "Titre":          45,
            "Ville":          20,
            "Statut contact": 18,
            "Agence":         25,
            "Message envoyé": 60,
        }

        for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
            lettre = get_column_letter(col_idx)
            ws.column_dimensions[lettre].width = largeurs_fixes.get(
                col_name, max(len(col_name) + 4, 12)
            )

        # Figer la première ligne
        ws.freeze_panes = "A2"
        # Filtre automatique sur toutes les colonnes
        ws.auto_filter.ref = ws.dimensions

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

            col_url = EXCEL_COLUMNS.index("URL") + 1  # index Excel (1-based)
            liens: set[str] = set()

            for row in ws.iter_rows(min_row=2, values_only=True):
                lien = row[col_url - 1]
                if lien:
                    liens.add(str(lien).strip())

            wb.close()
            logger.debug(f"{len(liens)} lien(s) déjà enregistré(s).")
            return liens

        except Exception as exc:
            logger.error(f"Erreur lecture Excel : {exc}")
            return set()

    # ── Écriture ──────────────────────────────────────────────────────────────

    @staticmethod
    def _na(valeur: str) -> str:
        """Retourne la valeur ou 'N/A' si vide."""
        v = (valeur or "").strip()
        return v if v else "N/A"

    def enregistrer_annonce(
        self,
        lien: str,
        type_bien: str,
        transaction: str,
        prix: str,
        prix_propose: str,
        surface: str,
        prix_m2: str,
        prix_m2_region_min: str,
        prix_m2_region_max: str,
        pieces: str,
        chambres: str,
        etage: str,
        ville: str,
        code_postal: str,
        dpe: str,
        ges: str,
        agence: str,
        telephone_agence: str,
        message_envoye: str,
        statut: str,
    ) -> None:
        """
        Ajoute une ligne dans le fichier Excel.
        Les champs vides sont remplacés par N/A.
        """
        na = self._na
        try:
            wb = openpyxl.load_workbook(self.path)
            ws = wb.active

            ligne = [
                na(lien),
                na(type_bien),
                na(transaction),
                na(prix),
                na(prix_propose),
                na(surface),
                na(prix_m2),
                na(prix_m2_region_min),
                na(prix_m2_region_max),
                na(pieces),
                na(chambres),
                na(etage),
                na(ville),
                na(code_postal),
                na(dpe),
                na(ges),
                na(agence),
                na(telephone_agence),
                na(message_envoye),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                na(statut),
            ]
            ws.append(ligne)

            # Ajuster la largeur des colonnes URL et Titre selon le contenu réel
            row_idx = ws.max_row
            for col_idx, valeur in enumerate(ligne, start=1):
                col_name = EXCEL_COLUMNS[col_idx - 1]
                if col_name in ("URL", "Titre", "Ville", "Statut contact") and valeur:
                    lettre = get_column_letter(col_idx)
                    largeur_actuelle = ws.column_dimensions[lettre].width or 10
                    nouvelle = min(len(str(valeur)) + 2, 80)
                    if nouvelle > largeur_actuelle:
                        ws.column_dimensions[lettre].width = nouvelle

            wb.save(self.path)
            logger.debug(f"Annonce enregistrée dans Excel : {lien}")

        except Exception as exc:
            logger.error(f"Impossible d'enregistrer dans Excel : {exc}")
