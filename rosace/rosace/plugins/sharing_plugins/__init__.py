"""
Package des sous-plugins de SHARING.

Un répertoire par sous-plugin, comme reduction_plugins/ — pas d'axe backend
(aucune dépendance matérielle). Contrairement à SEQUENCER/REDUCTION, un
programme n'en déclare qu'un seul (clé "sharing" singulière dans le YAML,
pas une liste) : un programme qui doit diffuser vers plusieurs destinations
gère ça dans son propre sous-plugin, ce n'est pas au mécanisme de le permettre.
Résolu dynamiquement par pipeline_loader.py::load_sharing_plugin() — ce
module n'importe rien lui-même.
"""
