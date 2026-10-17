# TODO

## Dernière mise à jour : 2026-10-17

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] Première pose d'une série DARK (science) pas franchement noire (un
      vrai spectre visible), alors que les poses suivantes de la même série
      sont bonnes (constaté 2026-09-19) — probable pose lancée trop tôt
      après le changement de position du spectro : `set_switch(sync=True)`
      n'attend que le retour à IDLE/OK du *vecteur* de propriété, qui peut
      déjà être dans cet état avant l'envoi (rien ne le repasse à BUSY
      localement tant que le driver n'a pas confirmé). Revu le 2026-10-12
      (Vega, DARK 5×180 s) : trace de Vega dans la 1re pose ; passage du
      spectro sur DARK et lancement de la pose dans la même seconde
      (18:36:53 UTC). Une fraction de seconde de lumière suffit sur une
      étoile brillante (4 s de pose science) — attendre la confirmation du
      driver, ou une pause de stabilisation comme `light_settle_seconds`
- [ ] Redémarrage du serveur INDI (KStars relancé) en pleine session : les
      devices de la session restent perdus jusqu'à la session suivante (un
      client INDI neuf n'est créé qu'au démarrage d'une session ou d'un test
      matériel) (2026-10-13)
- [ ] La monture AM3 arrête de suivre peu après le méridien : HD 26398
      (nuit du 14, partie à l'est, angle horaire −5,8° → +6,0° prévu) a
      perdu l'étoile vers 02:18 UT, angle horaire ≈ +1,3° ; 5 poses sur 14
      sans étoile, écart de 4,3° à la fin (rattrapé par `stack`). Le
      réglage provisoire `selector.meridian_tolerance_deg: 15` ne
      correspond donc pas à la monture telle qu'elle est réglée : choisir
      entre régler la limite de la monture (driver AM3 / Ekos) ou ramener
      ce réglage vers 1°. Rosace ne voit pas non plus l'arrêt du suivi
      pendant la série (16 min sans étoile sans aucun message) (2026-10-15)

## Gros chantiers

- [ ] Passer sur le RC8 sous abri : nouvel instrument, donc nouveau profil
      matériel (`hardware_indi_<…>.yaml` : caméras, monture, spectro,
      focuser, PHD2), nouvelle config (site, noms BeSS, réglages du guidage
      `slit_x`/`slit_y`/échantillonnage, bornes de réduction, réponse
      instrumentale, raies de la lampe), étapes propres à la monture
      (`sequencer_prefix`), et validation sur le ciel (2026-10-17)
- [ ] Page de validation des spectres Be avant envoi à BeSS : l'observateur
      voit chaque spectre (et son résumé PDF), l'accepte ou le refuse, et
      seuls les spectres acceptés partent vers BeSS. Pose la question du
      SHARING : envoi automatique en fin d'observation, ou différé après
      validation (un état « à valider / validé / envoyé » par spectre,
      conservé en base) — à cadrer avec la ligne « SHARING pour le
      programme Be » (2026-10-17)
- [ ] Simplifier CLAUDE.md (232 ko, ~940 lignes ; traduction française
      dans `~/Téléchargements/CLAUDE_fr_2026-10-17_v1.0.pdf`) : y garder les
      règles, conventions et l'architecture ; déplacer l'histoire (dates,
      incidents, options écartées) vers HISTORY.md et le détail des
      algorithmes (seuils, étapes, valeurs mesurées) vers les docstrings des
      modules ; corriger au passage les phrases périmées. Section par
      section, chaque changement validé par l'utilisateur. Objectif
      indicatif : taille divisée par 3 ou 4 (2026-10-17)

## Chantiers proposés

- [ ] Image Docker de la démo sur matériel rejoué (étape 5 du chantier
      démo) : astrometry.net et les 7 tuiles d'index dans l'image, jeu de
      rejeu copié depuis `docker/replay_data/`, lancement par `--demo-mode`,
      volume sur `~/.rosace-demo` ; supprimer `docker/config.demo.yaml`,
      `docker/demo_programs/`, `docker/seed_demo_data.py`, `docker/preview/`
      et réécrire `docker/README.md` (2026-10-16)
- [ ] Publier l'image Docker de la démo sur ghcr.io, et le jeu de rejeu en
      pièce jointe d'une release GitHub, pour qu'un autre poste n'ait pas à
      le reconstruire depuis `~/.rosace` (2026-10-16)
- [ ] Supprimer le backend `internal` (`sequencer_backend: internal`,
      `programs/demo.yaml`, les `_internal.py`) : la démo ne s'en sert plus.
      Basculer d'abord ses tests sur le matériel rejoué (2026-10-16)
- [ ] `--demo-mode` démarre toujours sur le port 8000 ; à côté du vrai
      serveur, il faut passer par `ROSACE_CONFIG=… uvicorn … --port 8001`.
      Option `--port` ? (2026-10-16)

- [ ] Flux et transparence dans la table `observations` : un résumé par
      observation, calculé par `calculate_flux` et recopié via
      `obs.measurements` — flux médian en ADU/s, dispersion du flux de
      l'étoile d'une pose à l'autre, flux de la pose la plus faible rapporté
      à la meilleure, nombre de poses mesurées / saturées, et nombre
      d'étoiles détectées dans les images de guidage ; le détail pose par
      pose reste dans `spectrum_flux` (2026-10-02). La nuit du 13 a montré
      que ce sont les bons indicateurs de nuages, et que le fond des zones de
      ciel des spectres ne l'est pas (surtout le halo de l'étoile)
      (2026-10-14)
- [ ] Distance à la Lune : filtre scheduler sur la distance de la cible à la Lune (seuil à définir, probablement selon la phase) — pas urgent
- [ ] Journalisation temporelle des étapes (time-series DB type InfluxDB). Définir les étapes que je veux suivre (pointing, science, focus, reference images... à compléter)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)
- [ ] Pour le calcul de foc, on peut pondérer la valeur moyenne de fWHM retenue en appliquant un coef de pondération proportionnel à la distance au centre du carré (100% au centre, 30% dans les coins). Pour durcir la valeur retenue.
- [ ] Une fois le focus en base : l'afficher (GUI ou export) en fonction de
      la température du focuser, pas seulement de l'heure/date — le focus
      dépend surtout de la température, un historique chronologique seul
      ne le montre pas (2026-09-25)
- [ ] Réduction : option pour garder ou non le répertoire des fichiers
      intermédiaires de chaque observation, et purge de ces répertoires
      (ARCHIVE ou CLOSE, à voir) — reconstructibles, rien de perdu (2026-10-06)
- [ ] Retraiter une observation ou toute une session (réduction relancée sur
      les fichiers existants) : un point d'entrée API REST, appelé depuis
      le GUI (2026-10-06)
- [ ] Tracer les pertes d'étoile de PHD2 : l'événement `StarLost` arrive déjà
      au client (`phd2_client.py::_dispatch_event()`) mais est ignoré. Un
      WARNING à la première perte, un INFO à la reprise (nombre de poses /
      durée perdues), un seul message par épisode. Ensuite : compter les
      poses de guidage perdues pendant chaque pose de science et l'enregistrer
      dans `obs.measurements` — indicateur de nuages, avec le fond de ciel
      (2026-10-14)
- [ ] Relire la configuration sans redémarrer le serveur. Aujourd'hui
      `config.py` la lit une seule fois au démarrage, 43 modules en copient
      les valeurs à leur import, et une quarantaine de sous-plugins figent
      leur part de `SUBPLUGIN_PARAMS` en constantes de module. Recharger le
      fichier ne suffit donc pas. Options : (a) bouton « Recharger » qui
      redémarre le processus lui-même, hors session seulement (simple, perd
      l'état en mémoire : démarrage/arrêt programmés) ; (b) vraie relecture :
      lire la config au moment de l'usage plutôt qu'à l'import (refonte
      d'une cinquantaine de fichiers). Dans les deux cas, refusé pendant une
      session (2026-10-14)
- [ ] Largeur de la trace en base : enregistrer la FWHM de la trace au
      centre du capteur (mesurée par `extract`, ou au même endroit) dans les
      mesures de chaque observation, à côté du FWHM du guidage et de la
      température, pour suivre la mise au point dans le temps. Avant cela :
      refaire la mesure par une nuit claire (trace 25 % plus large la nuit
      du 13 que la veille, focuser inchangé — focus ou turbulence ?)
      (2026-10-14)
- [ ] SHARING pour le programme Be (gros chantier) : aujourd'hui tous les
      programmes utilisent `blank_share`, qui ne fait que logger. Écrire le
      sous-plugin de partage de `Be_stars.yaml` pour transmettre les spectres
      à BeSS. Points à cadrer d'abord : mode de dépôt accepté par BeSS (à
      vérifier), envoi automatique ou après validation par l'observateur,
      critères de qualité avant envoi (spectre complet, transparence, flux
      suffisant), suivi de ce qui a été envoyé et accepté, et conformité de
      l'en-tête FITS (déjà vérifiée par `write_spectrum`) (2026-10-14)
- [ ] Graphe de guidage PHD2 par observation (écart en AD et en Déc, pose
      par pose, comme le graphe de PHD2). Étape 1, sans toucher à Rosace :
      lire le journal de guidage de PHD2 (`PHD2_GuideLog_*.txt`, sur la
      machine de PHD2, à copier sur le PC), le découper par observation avec
      les heures de début et de fin de guidage du log de Rosace, tracer un
      graphe par observation. Étape 2, si ça vaut la peine : Rosace enregistre
      lui-même les événements `GuideStep` reçus de PHD2 pendant chaque
      observation, graphe dans le résumé PDF et chiffres (dispersion du
      guidage) dans la base — à faire avec la ligne « pertes d'étoile de
      PHD2 » (2026-10-14)
- [ ] Étoile de guidage PHD2 loin de la fente et des bords : le 14 au soir,
      NGC 6543 a échoué parce que PHD2 guidait sur l'objet lui-même, à 5 px
      au-dessus de la fente ; amené dans la fente, il a disparu en partie et
      PHD2 ne s'est jamais stabilisé (180 s). PHD2 n'a pas de zone
      d'exclusion, mais `guide` et `find_star` acceptent une zone de
      recherche (`roi`, un rectangle [x, y, largeur, hauteur]) : la sélection
      automatique de l'étoile s'y limite. Piste : passer à `start_guiding()`
      un rectangle au-dessus ou au-dessous de la bande de la fente, marges
      aux bords comprises (choisir le côté le plus riche en étoiles, d'après
      l'image de guidage de Rosace). À vérifier sur le ciel : que les étoiles
      secondaires du mode multi-étoiles restent dans ce rectangle, et quelle
      marge aux bords PHD2 applique déjà de lui-même. Dans le même chantier :
      afficher sur le panneau « Guidage » (et dans le résumé PDF) la zone de
      recherche donnée à PHD2 et la position de l'étoile de référence qu'il a
      retenue (`get_lock_position()`, déjà lu par `pointing_72ed`) — un
      élément d'overlay de plus, de couleur distincte du point de la fente de
      `slit_xy` ; et vérifier, avant d'aller plus loin, que cette étoile n'est
      pas dans la bande de la fente (sinon WARNING, ou relancer la sélection)
      (2026-10-14)

## GUI

- [ ] GUI : indiquer « arrêt en cours » dans la zone start/stop, entre la
      demande d'arrêt (manuelle ou programmée) et la fin de CLOSE_SESSION —
      l'observation en cours va à son terme, ce qui peut prendre plus d'une
      demi-heure (2026-10-13)
      Revu 2026-10-17 : afficher clairement « Arrêt demandé » tant que
      l'observation en cours continue après la demande d'arrêt — à ce
      moment l'écran ne donne presque plus d'information sur ce qui se passe
- [ ] Étape SEQUENCER en jaune (avertissement) quand une étape non
      bloquante échoue — backend + GUI
- [ ] GUI : refaire l'ajustement automatique des seuils (étirement 1 %/99 %)
      quand une nouvelle image d'un autre type que la précédente arrive sur
      un panneau (science → dark → flat…) — aujourd'hui seule la 1re image
      reçue est ajustée, la suite se fait à la main ; le type peut se lire
      dans l'en-tête FITS (`IMAGETYP`) ou le préfixe du nom (2026-10-12)
- [ ] GUI web : l'affichage d'une nouvelle image (au moins panneau Spectre)
      repart en taille pleine à chaque nouvelle capture au lieu de garder
      le zoom courant (constaté 2026-09-19)
- [ ] GUI : inverser les panneaux Spectre 2D (gauche aujourd'hui) et
      Guidage (droite) — pas urgent (2026-10-11)
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] GUI : version anglaise de l'interface (libellés, messages, menus d'aide),
      avec choix de la langue — x4js a un module de traduction (`core_i18n`)
      à regarder (2026-10-11)

## Chantiers plus lointains

- [ ] Backend ASCOM / passage sous Windows — bloqué sur l'accès à un
      environnement Windows
- [ ] Deuxième instrument ("gros télescope", ex. UVEX) — driver
      IndiUsisSpectro existant, aucun profil matériel
- [ ] INIT_SESSION / CLOSE_SESSION adaptables par observatoire : aujourd'hui
      classes fixes (seul le SCHEDULER est choisi en config) ; rôles mount /
      science_camera / focuser et ambiante lue sur le focuser en dur — pistes :
      registre comme les schedulers, ou étapes de session déclarées dans le
      YAML matériel (comme sequencer_prefix) (idée 2026-10-05)

## Petits items

- [ ] Écrire le vrai type de pose dans les FITS : régler `CCD_FRAME_TYPE`
      d'INDI (Light / Dark / Bias / Flat) avant chaque série, pour que
      l'en-tête dise `IMAGETYP = 'Dark Frame'` etc. — aujourd'hui toutes les
      poses sont `'Light Frame'`, y compris darks, bias, flats et lampe (utile
      à Demetra et aux autres logiciels). À valider sur le vrai matériel
      (2026-10-17)
- [ ] Liste d'observation manuelle : accepter les lignes en commentaire
      (`#` en début de ligne), ignorées sans message, dans les deux lecteurs
      (`manual_list` du sélecteur et `scheduler_rosace_2026`). Aujourd'hui
      une telle ligne est bien ignorée, mais comme ligne invalide, avec une
      ERROR dans le journal (2026-10-17)
- [ ] Numéro de version de Rosace : `pyproject.toml` déclare `0.5.0` (repris
      dans le mot-clé FITS `SWCREATE` des spectres), mais ce numéro n'a
      jamais évolué. Définir une règle de numérotation (quand l'incrémenter),
      poser un tag git par version, et afficher la version (log au démarrage,
      GUI, `--version`) (2026-10-13)
- [ ] Commande `rosace` cassée : `[project.scripts]` de `pyproject.toml`
      pointe sur l'application FastAPI (`rosace.main:app`), pas sur une
      fonction de lancement — `rosace --help` échoue (TypeError). La faire
      pointer sur le lancement de `cli.py` (même comportement que
      `python -m rosace.main`), ou supprimer la déclaration (2026-10-11).
      Souhait de l'utilisateur : `rosace` seul démarre le serveur,
      `rosace --help` affiche l'aide (mêmes options que `python -m
      rosace.main`) (2026-10-11)
- [ ] flat_acquisition : la lampe flat chauffe encore pendant la série — 1re
      image à 88 % des suivantes, puis +1 % jusqu'à la 5e (nuit 2026-09-25).
      Le rejet min/max du master l'écarte, mais une pause de stabilisation
      avant la série (comme light_settle_seconds de slit_xy) serait plus
      propre (idée 2026-10-07)

## Réduction

- [ ] Trace pas tout à fait droite : sur HD 7636 (2026-10-14), droite entre
      x ≈ 250 et 1350 (−0,12°) mais remonte d'environ 2 px au bout bleu
      (x < 200). Un seul angle (tilt) ne la décrit pas exactement : l'angle
      mesuré dépend du poids donné au bout bleu (axes d'inertie −0,09°,
      bandes −0,14°). Étudier une correction courbe, ou ignorer le bout bleu
      (2026-10-16)

- [ ] tilt : le décalage s'applique pour l'instant à toute la hauteur de la
      colonne. À optimiser si c'est trop lent : se limiter à une bande autour
      de la trace (par exemple 200 px, assez pour garder le fond de ciel de
      la zone éclairée par le flat) (idée 2026-10-08)
- [ ] Rayons cosmiques : les traiter au prétraitement (détection sur l'image
      2D seule), en vérifiant que les raies de la lampe ne sont pas touchées
      (2026-10-09)
- [ ] Pouvoir de résolution R = λ / Δλ : le mesurer (par exemple sur la
      largeur à mi-hauteur des raies de la lampe étalonnée), étape encore à
      choisir ; Demetra écrit BSS_ITRP = 600 (valeur indicative de
      l'instrument) (2026-10-09)
- [ ] Summary : ajouter une image grand champ (avec la position de l'étoile
      observée) et une image Fripon, qui montreraient toutes deux s'il y
      avait des nuages — pour plus tard (2026-10-10)
- [ ] Mouvement propre : le pointage (slew_72ed, pointing_72ed) place sur la
      fente la position J2000 du catalogue, sans mouvement propre. Pour Vega
      (0,35"/an), l'étoile réelle est à 9,4" de ce point en 2026 ; pour la
      plupart des étoiles Be l'effet est faible, mais certaines ont un
      mouvement propre notable. Importer pmra/pmdec de Simbad dans targets.db
      et ramener la position à la date de l'observation (2026-10-10)
- [ ] Summary et log : séparer l'écart étoile–fente en composante le long de
      la fente (sans effet sur le flux) et en travers (pertes de lumière) ;
      seule la seconde compte pour le centrage (2026-10-10)
