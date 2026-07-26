"""
Package des sous-plugins du SEQUENCER.
Deux implémentations parallèles selon SEQUENCER_BACKEND (config.py) :
- internal/ : simulation interne, sans dépendance externe.
- indi/     : protocole INDI/PHD2 réel (placeholder, chantier suivant).
pipeline_loader._build_registry() choisit laquelle importer ; ce module
n'expose donc aucune classe concrète lui-même.
"""
