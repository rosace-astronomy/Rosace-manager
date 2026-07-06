"""
Package des sous-plugins du SEQUENCER.
Le pipeline est chargé dynamiquement depuis le YAML du programme actif.
Ce module n'expose que les classes individuelles ; c'est pipeline_loader
qui construit la liste ordonnée selon la configuration du programme.
"""

from .slew    import SlewPlugin
from .focus   import FocusPlugin
from .expose  import ExposePlugin

__all__ = ["SlewPlugin", "FocusPlugin", "ExposePlugin"]
