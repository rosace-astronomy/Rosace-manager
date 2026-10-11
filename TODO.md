# TODO

## Dernière mise à jour : 2026-09-25 08:15

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] Scheduler : reporter une cible après un échec (retentée aussitôt 2–4 fois)
- [ ] Étape SEQUENCER en jaune (avertissement) quand une étape non
      bloquante échoue — backend + GUI
- [ ] Première pose d'une série DARK (science) pas franchement noire (un
      vrai spectre visible), alors que les poses suivantes de la même série
      sont bonnes (constaté 2026-09-19) — probable pose lancée trop tôt
      après le changement de position du spectro : `set_switch(sync=True)`
      n'attend que le retour à IDLE/OK du *vecteur* de propriété, qui peut
      déjà être dans cet état avant l'envoi (rien ne le repasse à BUSY
      localement tant que le driver n'a pas confirmé)

## Chantiers proposés

- [ ] Flux dans la table `observations` : un résumé par observation
      (flux médian en ADU/s, dispersion entre poses, nombre de poses
      mesurées / saturées), calculé par `calculate_flux` et recopié via
      `obs.measurements` (phase B du scheduler multi-programmes) ; le détail
      pose par pose reste dans `spectrum_flux` (2026-10-02)
- [ ] Distance à la Lune : filtre scheduler sur la distance de la cible à la Lune (seuil à définir, probablement selon la phase) — pas urgent
- [ ] Temps de pose des étoiles faibles : la loi `exposure_time_mag0 ×
      10^(mag/2,5)` vise l'approche de la saturation. Au-delà de
      `max_exposure_time`, la durée est déjà découpée en poses de 180 s, mais
      le temps total explose pour une étoile faible (magnitude 9,8 avec 4 s à
      magnitude 0 : environ 33 600 s, soit 187 poses).
- [ ] Journalisation temporelle des étapes (time-series DB type InfluxDB). Définir les étapes que je veux suivre (pointing, science, focus, reference images... à compléter)
- [ ] Virer du fichier Observation les mesures de flux (uniquement en log et dans la base de données, et de manière plus synthétique)
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)
- [ ] Pour le calcul de foc, on peut pondérer la valeur moyenne de fWHM retenue en appliquant un coef de pondération proportionnel à la distance au centre du carré (100% au centre, 30% dans les coins). Pour durcir la valeur retenue.
- [ ] Dans `focus`, logger la température du focuser en même temps que la
      date/heure de la mesure de FWHM.
- [ ] Une fois le focus en base : l'afficher (GUI ou export) en fonction de
      la température du focuser, pas seulement de l'heure/date — le focus
      dépend surtout de la température, un historique chronologique seul
      ne le montre pas (2026-09-25)
- [ ] Faire un plan d'observation Be plus sophstiqué, pour ne pas refaire toujours les mêmes obserations. Prendre en compte les obserations récentes et les demandes de BeSS.
- [ ] Mettre en place un système qui permet de ne pas consommer les étoiles qui ont plantées. Peut-être calculer un facteur global de priorité
- [ ] Aller chercher toutes les Be dans BeSS. Et regarder le priorités d'observation BeSS. 
- [ ] Réduction : option pour garder ou non le répertoire des fichiers
      intermédiaires de chaque observation, et purge de ces répertoires
      (ARCHIVE ou CLOSE, à voir) — reconstructibles, rien de perdu (2026-10-06)
- [ ] Retraiter une observation ou toute une session (réduction relancée sur
      les fichiers existants) : un point d'entrée API REST, appelé depuis
      le GUI (2026-10-06)
- [ ] Mode démo : images réalistes (guidage avec fente et étoiles, vrai
      spectre) et spectre résultant affiché, même factice (2026-10-06)
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

- [ ] Commande `rosace` cassée : `[project.scripts]` de `pyproject.toml`
      pointe sur l'application FastAPI (`rosace.main:app`), pas sur une
      fonction de lancement — `rosace --help` échoue (TypeError). La faire
      pointer sur le lancement de `cli.py` (même comportement que
      `python -m rosace.main`), ou supprimer la déclaration (2026-10-11)

- [ ] GUI web : l'affichage d'une nouvelle image (au moins panneau Spectre)
      repart en taille pleine à chaque nouvelle capture au lieu de garder
      le zoom courant (constaté 2026-09-19)
- [ ] flat_acquisition : la lampe flat chauffe encore pendant la série — 1re
      image à 88 % des suivantes, puis +1 % jusqu'à la 5e (nuit 2026-09-25).
      Le rejet min/max du master l'écarte, mais une pause de stabilisation
      avant la série (comme light_settle_seconds de slit_xy) serait plus
      propre (idée 2026-10-07)

## Réduction

- [ ] tilt : le décalage s'applique pour l'instant à toute la hauteur de la
      colonne. À optimiser si c'est trop lent : se limiter à une bande autour
      de la trace (par exemple 200 px, assez pour garder le fond de ciel de
      la zone éclairée par le flat) (idée 2026-10-08)
- [ ] Rayons cosmiques : les traiter au prétraitement (détection sur l'image
      2D seule), en vérifiant que les raies de la lampe ne sont pas touchées
      (2026-10-09)
- [ ] Étape « Summary » en fin de réduction : une page de synthèse par
      observation (spectre en longueur d'onde avec les raies repères, lampe
      avec les raies de référence retenues et leurs résidus, loi de
      dispersion, domaine couvert) — sur le modèle du PDF de vérification de
      16 Peg (2026-10-09)
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
