# -*- coding: utf-8 -*-
"""Optional server libraries, and the exact command that installs them.

Nothing here is declared in the manifest's `external_dependencies`: Odoo
would then refuse the whole install over one missing file format (see the
note in __manifest__.py). Instead the settings page shows a ready-to-paste
`pip install` command for whatever is missing.
"""
import importlib.util
import shutil

# (pip distribution, import names — any one is enough, feature it unlocks).
OPTIONAL_LIBS = (
    ("numpy", ("numpy",), "shortlist"),
    ("python-docx", ("docx",), "docx"),
    ("openpyxl", ("openpyxl",), "xlsx"),
    ("pypdf", ("pypdf", "PyPDF2"), "pdf"),
    ("python-pptx", ("pptx",), "pptx"),
    ("py7zr", ("py7zr",), "7z"),
    ("rarfile", ("rarfile",), "rar"),
)

LIBREOFFICE_COMMAND = "sudo apt install libreoffice-writer libreoffice-impress"


def _importable(name):
    # find_spec does not execute the library, so this stays cheap enough to
    # run every time the settings page opens.
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def missing_libs():
    """[(pip name, feature key), …] for every optional library not found."""
    return [(pip, key) for pip, imports, key in OPTIONAL_LIBS
            if not any(_importable(name) for name in imports)]


def pip_command(packages):
    """`pip install …` for the missing packages, as shown on the settings page."""
    return "pip install " + " ".join(packages)


def libreoffice_missing():
    """Legacy .doc/.ppt files are converted with the LibreOffice binary."""
    return not (shutil.which("soffice") or shutil.which("libreoffice"))
