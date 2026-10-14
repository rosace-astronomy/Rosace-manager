# TODO

## Dernière mise à jour : 2026-09-25 08:15

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] Étape SEQUENCER en jaune (avertissement) quand une étape non
      bloquante échoue — backend + GUI
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
- [ ] Hauteur du Soleil : aucun contrôle dans Rosace aujourd'hui, il observe
      tant qu'on ne l'arrête pas. Ne pas observer tant que le Soleil est
      au-dessus d'une hauteur donnée (réglage d'installation), et terminer la
      session proprement à l'aube si aucun arrêt n'a été demandé — premier
      garde-fou d'un fonctionnement sans surveillance (2026-10-14)
- [ ] Indicateur de transparence par observation (élargit la ligne « Flux
      dans la table observations ») : dispersion du flux de l'étoile d'une
      pose à l'autre, flux de la pose la plus faible rapporté à la meilleure,
      nombre d'étoiles détectées dans les images de guidage — enregistrés
      dans `obs.measurements`. La nuit du 13 a montré que ce sont les bons
      indicateurs de nuages, et que le fond des zones de ciel des spectres
      ne l'est pas (surtout le halo de l'étoile) (2026-10-14)
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
- [ ] GUI : refaire l'ajustement automatique des seuils (étirement 1 %/99 %)
      quand une nouvelle image d'un autre type que la précédente arrive sur
      un panneau (science → dark → flat…) — aujourd'hui seule la 1re image
      reçue est ajustée, la suite se fait à la main ; le type peut se lire
      dans l'en-tête FITS (`IMAGETYP`) ou le préfixe du nom (2026-10-12)
- [ ] GUI : inverser les panneaux Spectre 2D (gauche aujourd'hui) et
      Guidage (droite) — pas urgent (2026-10-11)
- [ ] GUI : indiquer « arrêt en cours » dans la zone start/stop, entre la
      demande d'arrêt (manuelle ou programmée) et la fin de CLOSE_SESSION —
      l'observation en cours va à son terme, ce qui peut prendre plus d'une
      demi-heure (2026-10-13)
- [ ] Noms de fichiers avec `*` et espaces multiples : les noms Simbad des
      Be importées de BeSS (`*   8 Lac A`, `* bet Mon A`) passent tels quels
      dans `Observation.suggested_filename` → `20261013T185447_*___8_Lac_A.yaml`,
      spectre final, PNG/PDF/SVG, summary (29 fichiers la nuit du
      2026-10-13). Le `*` est un joker du shell (pénible à manipuler, refusé
      par Windows). Nettoyer le nom comme le fait déjà `intermediate_dir()`
      (lettres, chiffres, `_`, `-`), sans toucher aux fichiers passés
      (2026-10-14)

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
- [ ] stack : le rejet d'une pose sous 30 % de la *médiane* de la série ne
      filtre plus quand plus de la moitié des poses sont nuageuses (la médiane
      est elle-même une pose vide). HD 175511, nuit du 13 : 8 poses sans
      étoile sur 14, 3 écartées, 5 sommées dans le spectre (et comptées dans
      EXPTIME). Comparer chaque pose aux meilleures de la série plutôt qu'à
      la médiane (2026-10-14)
