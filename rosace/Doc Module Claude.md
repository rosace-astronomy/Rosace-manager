Pour lancer le serveur (truc produit avec Claud AI) : 

- Se placer dans le répertoire ~/Obs-St-Pancrasse/ROSACE/Rosace-manager
- Lancer l'environnement virtuel : source rosace-manager/bin/activate
- Se placer dans le répertoire où il y  a le pyproject.toml : ~/Obs-St-Pancrasse/ROSACE/Rosace-manager/rosace
- uvicorn rosace.main:app --reload
- Puis dans un browser web : localhost:8000
- Depuis le 14/07, l'interface graphique est servie par le même serveur (plus besoin d'un second terminal ni du port 8080) : elle est accessible directement sur http://localhost:8000/

Le 15/05/2026, je regarde plus en détail les options de Claude. Je tombe sur un site intéressant avec des astuces pour économiser des tokens :

https://www.blogdumoderateur.com/claude-astuces-economiser-tokens/

Maintenant que j'ai des tests (PyTest), je peux les lancer comme ça :
pytest rosace/test_rosace_core.py -v (dans le même répertoire que main.py)

