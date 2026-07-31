"""
Package des sous-plugins de REDUCTION.

Un répertoire par sous-plugin (bias/, dark/, flat/, extract/), chacun avec
sa classe et ses tests. Le pipeline est chargé dynamiquement par
pipeline_loader.py, un import par étape déclarée dans le programme YAML
actif — ce module n'importe donc rien lui-même.
"""
