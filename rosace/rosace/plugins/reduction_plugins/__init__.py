"""
Package des sous-plugins de REDUCTION.
Le pipeline est chargé dynamiquement depuis le YAML du programme actif.
Ce module n'expose que les classes individuelles ; c'est pipeline_loader
qui construit la liste ordonnée selon la configuration du programme.
"""

from .bias    import BiasPlugin
from .dark    import DarkPlugin
from .flat    import FlatPlugin
from .extract import ExtractPlugin

__all__ = ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]
