"""
Fixtures pytest partagées par tous les fichiers de tests du paquet
(test_rosace_core.py, plugins/test_*.py, plugins/*_plugins/test_*.py).
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def tmp_data_dir(tmp_path_factory):
    """
    Remplace BASE_DATA_DIR par un répertoire temporaire pour toute la session.
    Évite de polluer ~/.rosace ou les bases de données de production.
    """
    tmp = tmp_path_factory.mktemp("rosace_test_data")
    # Patch avant le premier import du package rosace
    import rosace.config as cfg
    cfg.BASE_DATA_DIR = tmp
    cfg.DATABASES_DIR = tmp
    cfg.DB_PATH       = tmp / "rosace.db"
    import rosace.targets as tgt
    tgt.DATABASES_DIR   = tmp
    tgt.TARGETS_DB_PATH = tmp / "targets.db"
    import rosace.database as db_mod
    db_mod.DATABASES_DIR = tmp
    db_mod.DB_PATH        = tmp / "rosace.db"
    return tmp
