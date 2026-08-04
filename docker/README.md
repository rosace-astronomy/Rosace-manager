# Rosace — module de démo Docker

Ce dossier contient tout ce qu'il faut pour faire tourner une **démo** de Rosace
dans un conteneur Docker : GUI déjà compilée, backend simulé
(`sequencer_backend: internal`), catalogue d'étoiles pré-rempli, compte admin
créé automatiquement. Ce n'est **pas** la façon dont une vraie installation
observatoire est censée tourner (voir `Dockerfile`) — c'est uniquement pour
permettre à quelqu'un d'essayer Rosace en quelques commandes, sans rien
installer d'autre que Docker.

## Prérequis

Installer Docker (une seule fois) :
- **Linux** : `docker` via le gestionnaire de paquets de la distribution, ou
  https://docs.docker.com/engine/install/
- **Windows / Mac** : Docker Desktop — https://www.docker.com/products/docker-desktop/

Vérifier que ça fonctionne : `docker --version`

## Construire l'image

Depuis la racine du dépôt (là où se trouve le `Dockerfile`) :

```
docker build -t rosace-demo .
```

- `-t rosace-demo` : nom donné à l'image, pour la retrouver ensuite.
- `.` : construire à partir du dossier courant.

Première construction : une à deux minutes (téléchargement des dépendances
Python et Node.js, compilation du GUI). Les fois suivantes, si le code n'a
pas changé, Docker réutilise ce qu'il a déjà — beaucoup plus rapide.

## Lancer le conteneur

```
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

- `-d` : lancer en arrière-plan.
- `-p 8080:8000` : port 8080 de la machine hôte relié au port 8000 du
  conteneur — ouvrir ensuite http://localhost:8080. Si le port 8080 est déjà
  pris chez toi, changer le premier nombre (ex. `-p 8081:8000`).
- `-v rosace-data:/data` : volume nommé, où sont gardés le catalogue
  d'étoiles, les comptes et les observations — **survit** à l'arrêt ou à la
  suppression du conteneur. Sans ce `-v`, tout serait perdu à chaque
  redémarrage.
- `--name rosace-demo` : nom donné au conteneur, pour le désigner facilement
  dans les commandes suivantes.

## Se connecter

http://localhost:8080 (ou le port choisi ci-dessus)

- Utilisateur : `rosace`
- Mot de passe : `rosace`

Identifiants de démo créés automatiquement au premier démarrage
(`docker/seed_demo_data.py`) — volontairement simples, à ne jamais faire pour
une vraie installation exposée sur un réseau. Un petit catalogue d'étoiles
(seedé depuis `docker/seed_demo_data.py`) est déjà prêt : cliquer sur START
dans l'interface lance directement une session d'observation simulée.

## Commandes utiles

| Action | Commande |
|---|---|
| Voir les logs en direct | `docker logs -f rosace-demo` |
| Arrêter le conteneur | `docker stop rosace-demo` |
| Le relancer (mêmes données) | `docker start rosace-demo` |
| Le supprimer (garde les données du volume) | `docker rm -f rosace-demo` |
| Repartir de zéro (supprime aussi les données) | `docker volume rm rosace-data` |

## Mettre à jour après une modification du code

`docker build` reconstruit l'**image**, mais ne touche pas au **conteneur**
déjà lancé (qui continue de tourner sur l'ancienne version). Après chaque
modification (Python ou GUI/X4) :

```
docker build -t rosace-demo .
docker rm -f rosace-demo
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

Le volume `rosace-data` étant conservé, les données (catalogue, comptes,
observations) survivent à la mise à jour — seule l'application change,
comme réinstaller un logiciel en gardant son dossier de configuration.

## Problème : "port is already in use" / "container name already in use"

- **Port déjà utilisé** (ex. une instance Rosace tourne déjà en local sur le
  8000) : changer le port hôte dans `-p`, ex. `-p 8081:8000`.
- **Nom de conteneur déjà pris** (`Conflict. The container name "/rosace-demo"
  is already in use`) : un conteneur de ce nom existe déjà, probablement d'un
  essai précédent.
  - Pour le réutiliser tel quel : `docker start rosace-demo`
  - Pour repartir propre : `docker rm rosace-demo` puis relancer la commande
    `docker run` ci-dessus.

## Transmettre l'image sans registre (ex. tester sur un autre PC/Mac)

Avant toute publication sur un registre (Docker Hub, GitHub Container
Registry...), l'image peut être transmise comme un simple fichier — clé USB,
email, drive partagé — sans connexion Internet nécessaire au moment du
transfert.

Sur la machine qui a construit l'image :

```
docker save rosace-demo | gzip > rosace-demo.tar.gz
```

Produit un fichier `.tar.gz` d'environ 700 Mo.

Sur la machine qui reçoit le fichier (Docker Desktop installé, rien
d'autre) :

```
docker load -i rosace-demo.tar.gz
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo rosace-demo
```

`docker load` réimporte l'image sous le même nom (`rosace-demo`), puis ça se
lance exactement comme sur la machine d'origine.

**Nuance multi-plateforme** : l'image est construite pour l'architecture de
la machine qui a fait `docker build` (typiquement `amd64`/Intel). Sur un Mac
Apple Silicon (M1/M2/M3...), Docker Desktop la fait quand même tourner via un
mode d'émulation intégré — plus lent, mais fonctionnel sans rien configurer.
Pour une image native (plus rapide) sur Apple Silicon, il faut soit la
reconstruire directement sur ce Mac, soit utiliser `docker buildx` pour
produire une image multi-architecture en une fois (raffinement à faire plus
tard, pas nécessaire pour un premier test).

## Publication future (pas encore fait)

Le dépôt étant sur GitHub (`rosace-astronomy/Rosace-manager`), GitHub
Container Registry (`ghcr.io`) serait l'option naturelle pour une diffusion
publique : gratuit pour un dépôt public, lié au compte GitHub existant. Une
fois publiée, n'importe qui pourrait faire directement :

```
docker pull ghcr.io/rosace-astronomy/rosace-demo:latest
docker run -d -p 8080:8000 -v rosace-data:/data --name rosace-demo ghcr.io/rosace-astronomy/rosace-demo:latest
```

— sans `git clone`, sans build local. Peut être automatisé avec une GitHub
Action qui reconstruit et republie l'image à chaque push. Non fait pour
l'instant, sur décision explicite (voir discussion).
