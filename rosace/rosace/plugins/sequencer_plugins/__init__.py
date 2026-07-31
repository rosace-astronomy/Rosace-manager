"""
Package des sous-plugins du SEQUENCER.

Un répertoire par sous-plugin (slew/, focus/, expose/), chacun avec sa
classe et ses tests. Le nom nu (<name>/<name>.py) est la version canonique,
utilisée quand SEQUENCER_BACKEND="indi" ; "internal" est une exception de
démo (<name>/<name>_internal.py, uniquement là où c'est utile — pas un
backend de plein droit). pipeline_loader.py résout dynamiquement, un import
par étape déclarée dans le programme YAML actif — ce module n'importe donc
rien lui-même.
"""
