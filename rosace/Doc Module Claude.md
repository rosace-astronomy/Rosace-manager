Pour lancer le serveur (truc produit avec Claud AI) : 

- Se placer dans le répertoire ~/Obs-St-Pancrasse/ROSACE/Rosace-manager
- Lancer l'environnement virtuel : source rosace-manager/bin/activate
- Se placer dans le répertoire où il y  a le pyproject.toml : ~/Obs-St-Pancrasse/ROSACE/Rosace-manager/rosace
- uvicorn rosace.main:app --reload
- Puis dans un browser web : localhost:8000
- L'interface graphique (créée le 16/05) se lance dans un autre terminal (au même endroit et avec le même environnement virtuel) par : python rosace/rosace_gui.py
  - et on accède à l'interface par http://localhost:8080/ dans un browser

Le 15/05/2026, je regarde plus en détail les options de Claude. Je tombe sur un site intéressant avec des astuces pour économiser des tokens :

https://www.blogdumoderateur.com/claude-astuces-economiser-tokens/

Maintenant que j'ai des tests (PyTest), je peux les lancer comme ça :
pytest rosace/test_rosace_core.py -v (dans le même répertoire que main.py)

