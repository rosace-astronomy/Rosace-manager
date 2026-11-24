# TODO

## Dernière mise à jour : 2026-11-22 20:54

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
- [ ] Résumé et page `/be/` sans image de guidage quand le contrôle de fin
      de `stop_phd2_guiding` échoue : `summary.guide_image()` se rabat sur
      `pointing_72ed["corrected_guide_image"]`, mais `pointing_72ed`
      enregistre `guide_image` — le repli ne marche jamais (HD 206773,
      nuit du 27) ; puis `--build-validation-data --apply` (2026-10-28)
      
## Gros chantiers

- [ ] Passer sur le RC8 sous abri : nouvel instrument, donc nouveau profil
      matériel (`hardware_indi_<…>.yaml` : caméras, monture, spectro,
      focuser, PHD2), nouvelle config (site, noms BeSS, réglages du guidage
      `slit_x`/`slit_y`/échantillonnage, bornes de réduction, réponse
      instrumentale, raies de la lampe), étapes propres à la monture
      (`sequencer_prefix`), et validation sur le ciel (2026-10-17)
- [ ] Simplifier CLAUDE.md (232 ko, ~940 lignes ; traduction française
      dans `~/Téléchargements/CLAUDE_fr_2026-10-17_v1.0.pdf`) : y garder les
      règles, conventions et l'architecture ; déplacer l'histoire (dates,
      incidents, options écartées) vers HISTORY.md et le détail des
      algorithmes (seuils, étapes, valeurs mesurées) vers les docstrings des
      modules ; corriger au passage les phrases périmées. Section par
      section, chaque changement validé par l'utilisateur. Objectif
      indicatif : taille divisée par 3 ou 4 (2026-10-17)
- [ ] Supprimer l'étape SHARING ? Question à laisser décanter : est-elle
      encore nécessaire ? L'envoi vers BeSS se fait après validation, donc
      en différé par rapport aux observations, et la page de validation
      sera une étape de REDUCTION de `Be_stars.yaml`. Une étape SHARING
      exécutée automatiquement juste après la réduction n'aurait alors plus
      de rôle. La retirer touche la machine à états (`main.py`),
      `models.py` (casier `sharing_data`), `rosace.db`
      (`sharing_status`/`sharing_done`), la clé `sharing:` de tous les
      programmes, `sharing_plugins/`, la GUI et les tests ; même question
      pour ARCHIVE, encore un espace réservé (2026-10-17)
- [ ] Revoir où Rosace range ses fichiers : tout est sous `~/.rosace/`
      (`base_data_dir`), sauf la configuration de l'installation et ses
      profils matériels, partis dans `~/.config/rosace/` le 2026-10-25 —
      peu naturel. Réflexion de fond à mener : séparer ce qui se règle
      (configuration, profils, listes d'observation, horizon, raies de
      référence, réponse) de ce que Rosace produit (bases, journaux,
      observations, bibliothèque de darks, caches), et décider où va
      chacun (2026-10-28)

## Chantiers proposés


- [ ] Plus tard : construction automatique de l'image à chaque push
      (Forgejo Actions / GitHub Actions) — télécharger le jeu de rejeu
      demande un jeton tant que le dépôt est privé (2026-10-25)
- [ ] Démarrer les simulateurs INDI et PHD2 depuis Rosace (serveur INDI
      avec le profil « simulators », PHD2 avec son profil équivalent) pour un
      premier essai sans les avoir lancés à la main — aujourd'hui, un premier
      lancement échoue tant qu'ils ne tournent pas en tâche de fond ; leur
      installation reste à faire à part (2026-10-25)
- [ ] Sortir aussi `programs/*.yaml` du code (réglages propres à
      l'installation, comme les parts de temps de `Be_stars.yaml`) : la démo
      les utilise et le sélecteur lit tout un dossier, à concevoir (2026-10-25)
- [ ] Supprimer le backend `internal` (`sequencer_backend: internal`,
      `programs/demo.yaml`, les `_internal.py`) : la démo ne s'en sert plus.
      Basculer d'abord ses tests sur le matériel rejoué (2026-10-16)

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
- [ ] Bilan de temps d'une nuit (performance = temps d'obturateur science
      ouvert / temps où Rosace est actif) : plan validé, à réaliser —
      catégorie déclarée par chaque sous-plugin, intervalles enregistrés par
      l'orchestrateur dans `rosace.db`, bilan par nuit (table, CSV, PDF A4
      d'une page) produit par CLOSE_SESSION, guide du `blank_seq` mis à
      jour. Plan :
      `~/.claude/plans/ok-a-me-va-mellow-crayon-of-f077e402-c8e3-464d-9195-e0e5e0499deb.md`,
      prototype : `~/Téléchargements/night_time_budget_prototype.py`
      (16/10 : 87,7 % d'obturateur ouvert) (2026-11-23)
- [ ] Bilan de temps des nuits passées : `rosace --build-time-budget <date>
      [--apply]` reconstruisant `time_segments` et `night_time_budget` à
      partir des `session.log` (logique du prototype
      `~/Téléchargements/night_time_budget_prototype.py` ; déplacement et
      stabilisation PHD2 estimés depuis le log, ces mesures n'existant pas
      avant), pour avoir l'historique depuis septembre (2026-11-24)
- [ ] Documentation Sphinx (dépôt `Rosace-docs`) : ajouter le bilan de temps
      d'une nuit et `TIME_CATEGORY` — texte prêt dans
      `~/Téléchargements/sphinx_time_category.rst` (un point dans la liste des
      principes de `Modules/Module3.rst`, une courte section dans la même
      page, un paragraphe pour `Contribute.rst` quand la partie « écrire un
      plugin » sera rédigée) (2026-11-24)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)
- [ ] Pour le calcul de foc, on peut pondérer la valeur moyenne de fWHM retenue en appliquant un coef de pondération proportionnel à la distance au centre du carré (100% au centre, 30% dans les coins). Pour durcir la valeur retenue.
- [ ] Une fois le focus en base : l'afficher (GUI ou export) en fonction de
      la température du focuser, pas seulement de l'heure/date — le focus
      dépend surtout de la température, un historique chronologique seul
      ne le montre pas (2026-09-25)
- [ ] Retraiter une observation ou toute une session (réduction relancée sur
      les fichiers existants) : un point d'entrée API REST, appelé depuis
      le GUI (2026-10-06)
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
- [ ] Focus et température : sur les médianes des 7 nuits du 27/09 au
      29/10, FWHM du guidage +0,21″/°C (r = +0,78) et largeur de la trace
      +0,18 px/°C (r = +0,86), focuser fixe ; à l'intérieur d'une nuit le
      lien est moins net (graphe
      `~/Téléchargements/Rosace_focus_temperature_2026-09-27_2026-10-29_v1.0.pdf`).
      1) Une nuit d'essai : faire varier la position du focuser à
      température à peu près constante, pour séparer focus et turbulence.
      2) Si le lien se confirme : compensation en température du focuser
      (pas de focuser par °C) (2026-10-30)
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

- [ ] Étape SEQUENCER en jaune (avertissement) quand une étape non
      bloquante échoue — backend + GUI
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] GUI : version anglaise de l'interface (libellés, messages, menus d'aide),
      avec choix de la langue — x4js a un module de traduction (`core_i18n`)
      à regarder (2026-10-11)
- [ ] GUI : écran de gestion du catalogue (programmes, import d'étoiles
      depuis Simbad, liens, temps de pose) — aujourd'hui seulement par la
      page API `/docs` ; demandé par un utilisateur externe (2026-10-31)

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

- [ ] Supprimer `~/ROSACE/analyses/` (analyse de la réponse
      instrumentale, 3,5 Go, créé par Claude hors du dépôt) une fois le
      travail sur la RI terminé (2026-10-28)
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
- [ ] flat_acquisition : la lampe flat chauffe encore pendant la série — 1re
      image à 88 % des suivantes, puis +1 % jusqu'à la 5e (nuit 2026-09-25).
      Le rejet min/max du master l'écarte, mais une pause de stabilisation
      avant la série (comme light_settle_seconds de slit_xy) serait plus
      propre (idée 2026-10-07)
- [ ] Masque d'horizon trop bas vers le nord : le 2026-10-30 à 18:34 UT,
      del UMi (az 355,7°, haut. 46,9°) était dans la maison — champ en partie
      reconnu, pas d'étoile dans la fente — alors que `Horizon St-Pan.txt`
      donne 46° à cet azimut. Remesurer le bord de la maison entre ~340° et
      10° et corriger le fichier (hors session) (2026-10-30)
- [ ] Bibliothèque de darks : une option (commande `rosace`, ou bouton) pour
      copier les darks d'une nuit dans `dark_library/`, dans le dossier de
      leur température (`TempMinus5degrees`...), lue dans le nom des fichiers
      comme le fait déjà `dark_acquisition`. Aujourd'hui seul un transfert à
      la main les y met. À préciser : quelles séries (la plus récente, toutes
      les complètes), que faire d'une série déjà présente, vérification de la
      taille et de l'EXPTIME (2026-10-30)
- [ ] Liste d'observation : une commande qui vérifie à l'avance, pour une
      nuit donnée, chaque ligne fenêtrée avec les contrôles mêmes du
      sélecteur (`checks.py` : hauteur, masque d'horizon, méridien), sans
      démarrer de session. Le 2026-10-30, une trentaine de lignes RI
      (HD 172728 après 20:00, Vega après 19:55, del UMi après 19:50) étaient
      impossibles d'avance à cause du masque NO et N (2026-10-31)
- [ ] Démo : le WARNING « Replay dataset not found » (`demo_seed.py`) dit
      de construire le jeu avec `scripts/build_demo_dataset.py`, ce qu'un
      utilisateur ne peut pas faire (il faut les données de l'observatoire) ;
      il devrait renvoyer au téléchargement de la release
      `demo-data-2026-10-14` (2026-10-31)

## Réduction

- [ ] Trace pas tout à fait droite : sur HD 7636 (2026-10-14), droite entre
      x ≈ 250 et 1350 (−0,12°) mais remonte d'environ 2 px au bout bleu
      (x < 200). Un seul angle (tilt) ne la décrit pas exactement : l'angle
      mesuré dépend du poids donné au bout bleu (axes d'inertie −0,09°,
      bandes −0,14°). Étudier une correction courbe, ou ignorer le bout bleu
      (2026-10-16)
- [ ] Rayons cosmiques : les traiter au prétraitement (détection sur l'image
      2D seule), en vérifiant que les raies de la lampe ne sont pas touchées
      (2026-10-09)
- [ ] Mouvement propre : le pointage (slew_72ed, pointing_72ed) place sur la
      fente la position J2000 du catalogue, sans mouvement propre. Pour Vega
      (0,35"/an), l'étoile réelle est à 9,4" de ce point en 2026 ; pour la
      plupart des étoiles Be l'effet est faible, mais certaines ont un
      mouvement propre notable. Importer pmra/pmdec de Simbad dans targets.db
      et ramener la position à la date de l'observation (2026-10-10)
- [ ] Summary et log : séparer l'écart étoile–fente en composante le long de
      la fente (sans effet sur le flux) et en travers (pertes de lumière) ;
      seule la seconde compte pour le centrage (2026-10-10)
- [ ] `guiding_stats` compte aussi les images de guidage prises après la
      perte de l'étoile : HD 212044 (nuit du 27, suivi arrêté au méridien)
      donne `GUIDRMS = 1311.26` dans le FITS livré. Limiter aux poses
      retenues par `stack`, ou signaler un guidage perdu — à discuter
      (2026-10-28)
- [ ] `--purge-simulated` ne supprime que les fichiers du haut du répertoire
      de session : les sous-répertoires de réduction des observations
      (`<nom>_<id>/`) restent (1,9 Go laissés par le salon le 2026-11-01).
      Les supprimer avec leur observation (2026-11-01)
- [ ] Réponse instrumentale et atmosphérique (RI × RA), une fois la RI
      établie par la campagne dédiée : dans le résumé de chaque
      observation, un graphe montrant la RI et les trois courbes de RA
      (Rayleigh, aérosols, ozone), avec la valeur de l'AOD utilisée
      (2026-11-22)
- [ ] Observer systématiquement une étoile A0V (de préférence CALSPEC) en
      début de nuit pour mesurer l'AOD de la nuit, et garder en base les
      résultats de toutes les A0V observées (AOD, masse d'air, forme
      mesurée), pour des statistiques à terme sur la RI et la RA
      (2026-11-22)
- [ ] Pour chaque A0V observée, enregistrer en base l'AOD mesurée (τ₅₅₀)
      et l'AOD donnée par CAMS au même instant et au même endroit, pour
      suivre dans la durée la convergence des deux valeurs (2026-11-22)
- [ ] Comprendre la lumière diffuse en fond d'image des flats : hors de la
      bande éclairée par la fente, les poses de flat reçoivent 700 à 1 200 ADU
      au-dessus du bias (5 à 6 % du signal), et sous 3950 Å le flat n'est
      presque que cette lumière. D'où vient-elle (diffusion dans le
      spectrographe, fuite de lumière du module de calibration) ? Faut-il la
      retirer du flat dans l'étape master_flat ? Analyse :
      ~/ROSACE/analyses/reponse_instrumentale/doc/rapport_flats.md (2026-11-22)
