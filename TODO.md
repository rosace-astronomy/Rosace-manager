# TODO

## Dernière mise à jour : 2026-09-25 08:15

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] Réduire `SETTLE_AFTER_STOP_SECONDS` (`stop_phd2_guiding`, 5 s
      aujourd'hui) si plusieurs nuits sans « USB reset » confirment la piste
- [ ] Scheduler : reporter une cible après un échec (retentée aussitôt 2–4 fois)
- [ ] Détecter une image de guidage « sans fond » (niveau ≈ noir) et
      mettre le scheduler en attente
- [ ] Étape SEQUENCER en jaune (avertissement) quand une étape non
      bloquante échoue — backend + GUI
- [ ] Focus : 0/11 mesures réussies — élargir fenêtre/roundness, SNR ≈ 5–10
- [ ] Mesurer l'horizon réel (est az 55–100°, nord az ~15°)

- [ ] **URGENT** — bêta CMi saturé (spectre) : le temps de pose à mag0
      (`exposure_time_mag0_seconds`) est probablement trop long pour les
      étoiles brillantes du programme — revoir le calcul pour bêta CMi et
      vérifier les autres cibles brillantes du plan d'observation
      (constaté 2026-09-20)
- [ ] Mettre en place un calcul d'intensité du spectre "universel" (qui servira de mesure 
      pour la suite). Peut-être sortir la puissance totale (tous les pixels non noirs) et la valeur max. Idé de départ : faire une première passe avec un filtre médian pour virer les pixels chauds, détecter la zone de la fente, puis faire une détection de seuil du fond dimage à 3 sigmas. A creuser.
- [ ] Calcul d'intensité du spectre : reprendre la définition des puissances
      (`spectrum_flux_helpers.py`). Le « max colonne » actuel
      (`peak_intensity`) est le pic de la coupe transversale (rapport ~7
      avec la puissance totale), pas le max de la somme de chaque colonne
      le long de la dispersion (rapport ~480) : à redéfinir avec
      l'utilisateur, puis régénérer le rapport PDF de la nuit du 23/24
      (constaté 2026-09-24)
- [ ] Rapport de flux : tracer la loi de pose en magnitude
      (`exposure_time_mag0 × 10^(mag/2.5)`, plafond `max_exposure_time`)
      à côté des mesures, sur les graphes en fonction de la magnitude
      (aujourd'hui seule la pente ajustée est dessinée) (2026-09-24)
- [ ] Réajuster `exposure_time_mag0_seconds` (1,0 s dans `config72ED.yaml`)
      d'après les mesures : le pixel max de la trace n'atteint que 14-16 %
      de la saturation pour la plupart des étoiles (nuit 23/24), et la pente
      mesurée par seconde (−0,33 à −0,36 dex/mag) est plus plate que la loi
      (−0,40). Vérifier aussi les poses imposées dans la base (HD 250240 :
      180 s × 20, très en dessous de la tendance) (2026-09-24)
- [ ] **CODÉ (2026-09-20), à valider sur le ciel** — Mise au point de la
      caméra de guidage qui dérive en cours de nuit
- [ ] Caméra grand champ (`pointing_camera`) : câble/port USB3 défectueux
      — diagnostic terminé (matériel confirmé), remplacement du câble/port
      encore à faire sur place. `journalctl -k` montre `usb
      2-2-port3: Cannot enable. Maybe the USB cable is bad?` / `config
      error` en boucle continue (~1/s) sur le port du hub USB3 ASM1074 où
      elle est branchée : échec de négociation électrique persistant, pas
      un souci logiciel Stellarmate. Distinct de l'incident du 18/09
      (caméra de guidage + Atik, sur le hub USB2 séparé) — deux pannes
      physiques indépendantes, pas une cause commune. Reste à faire (pas
      encore fait) : remplacer le câble/changer de port sur place. Pour
      mémoire, rien dans le code n'utilise `pointing_camera` aujourd'hui
      (grep sur tout `rosace/`) — un correctif YAML (retirer/commenter
      l'entrée) reste possible en attendant si besoin de démarrer une
      session sans cette caméra, mais l'utilisateur préfère régler la
      vraie cause plutôt que contourner
- [ ] Confirmer/écarter la cause du bug de réutilisation dark/bias/calib/flat
      (log DEBUG frame_size en place depuis le 2026-09-18)
- [ ] `guiding_dark_acquisition` n'a aucun contrôle de fraîcheur/qualité :
      un dark de guidage pris de jour (ciel encore lumineux) ressort saturé
      et, une fois soustrait, rend toutes les images de guidage suivantes
      quasi noires — réfléchir à un garde-fou (détection de saturation/niveau de
      fond anormal, contrôle horaire, etc.)
- [ ] Pointage près du pôle : _slew_with_backlash_approach() envoie
      dec + backlash_offset_deg, ce qui dépasse 90° pour une cible de
      déclinaison > 88° (ex. Polaris, 89,3°) — comportement de la monture
      non vérifié, probablement un bug
- [ ] Première pose d'une série DARK (science) pas franchement noire (un
      vrai spectre visible), alors que les poses suivantes de la même série
      sont bonnes (constaté 2026-09-19) — probable pose lancée trop tôt
      après le changement de position du spectro : `set_switch(sync=True)`
      n'attend que le retour à IDLE/OK du *vecteur* de propriété, qui peut
      déjà être dans cet état avant l'envoi (rien ne le repasse à BUSY
      localement tant que le driver n'a pas confirmé)
- [ ] Messages d'erreur "[ERROR] Failed to set temperature (1)" répétés
      chaque minute dans le Journal (et rosace.log) après un arrêt de
      session en plein refroidissement de la caméra : le driver Atik
      continue de gérer la température alors que CLOSE_SESSION a déconnecté
      la caméra (cause confirmée 2026-09-19, sans gravité : INIT_SESSION
      reconnecte la caméra au démarrage suivant). Fonctionnel, mais pas
      propre — pistes : remettre la consigne à l'ambiante avant la
      déconnexion, ou attendre la fin du refroidissement (voir l'item
      "Décider si CLOSE_SESSION doit attendre…" plus bas)
- [ ] Caméra de guidage ASI120MM Mini (nuit 2026-09-24/25 perdue) : signaler à
      indilib/indi-3rdparty que `indi_asi_ccd` (indi-asi 2.4+z, libasi 1.37)
      fait un `USBDEVFS_RESET` à chaque exposition, sans propriété `USB_RESET`
      pour l'éviter (preuve : `strace` + journal noyau du Stellarmate) —
      piste retenue en priorité sur un correctif local (2026-09-25) ; rapport
      soigné (versions exactes, logs, reproduction) avant envoi
- [ ] Hypothèse à tester : le `USBDEVFS_RESET` par exposition de l'ASI120 (hub
      USB2 `ASM1074` partagé avec l'Atik) perturberait le transfert de l'Atik
      (2026-09-18, `Image size is unexpected`) et peut-être le lien PHD2
      (2026-09-19/20) — comparer `journalctl -k` du Stellarmate avec les
      incidents Atik/PHD2 ; brancher l'ASI120 sur un autre hub/port pour voir
- [ ] Essayer une `BandWidth` USB plus basse sur l'ASI120 (`CCD_CONTROLS`,
      40 aujourd'hui) — simple essai, effet sur les échecs de réouverture non prouvé
- [ ] Évaluer une autre caméra de guidage (autre fabricant) et/ou un hub USB à
      alimentation commandable par port (`uhubctl`) pour couper/rallumer la
      caméra à distance — un changement de caméra invalide `sampling_arcsec`,
      `slit_x`/`slit_y`, gain/offset, darks et réglages de `focus`
- [ ] Erreur de pointage initiale d'environ 43′ d'un côté du pied (Vega,
      Sheliak, Aigle, Cygne, Ophiuchus en soirée) contre 1-13′ de l'autre,
      depuis le 2026-09-19 ; environ 20′ avant. Ne bloque pas le plate
      solve (rayon 5°) mais a doublé — à comprendre (2026-09-26)
- [ ] 4 Aql : vérifier sur le ciel que `--downsample 2` suffit (solvé hors
      ligne sur les images du 23 et du 25/09, pas sur celle du 24/09) ; si
      ça échoue encore, pistes : pose de guidage plus longue, gain (2026-09-26)

## Chantiers proposés

- [ ] Distance à la Lune : filtre scheduler sur la distance de la cible à
      la Lune (seuil à définir, probablement selon la phase) — pas urgent
- [ ] Temps de pose des étoiles faibles : la loi `exposure_time_mag0 ×
      10^(mag/2,5)` vise l'approche de la saturation. Au-delà de
      `max_exposure_time`, la durée est déjà découpée en poses de 180 s, mais
      le temps total explose pour une étoile faible (magnitude 9,8 avec 4 s à
      magnitude 0 : environ 33 600 s, soit 187 poses). Pistes : valeur en dur
      par étoile dans `program_stars.exposure_time` (existe déjà, cas de
      HD 250240), plafond de temps total par observation, voire un rapport
      signal/bruit visé, calculé à partir des mesures de `spectrum_flux`
      (2026-09-26)
- [ ] Journalisation temporelle des étapes (time-series DB type InfluxDB)
- [ ] `export_demetra` : produire un seul zip pour toute la session au lieu
      d'un zip par observation (après le chantier `calculate_flux`) (2026-09-26)
- [ ] Pipeline de réduction natif Rosace — bloqué sur des exemples XML
      Demetra
- [ ] Calcul du centre de la fente.
- [ ] Centre de la fente : première mesure (2026-09-24, images de guidage de
      Vega, `find_axes(invert=True)`) → Y = 617,2 à X = 613, contre
      `slit_y: 614` dans `config72ED.yaml` (+3,2 px ≈ 5,6″ ; la valeur
      actuelle tombe sur le bord bas de la fente, qui occupe les lignes
      613,6 à 620,4). Fente inclinée de +1,53° (Y croît avec X) : le Y n'a
      de sens qu'à la colonne `slit_x`. À FAIRE après la nuit (le serveur
      lit `slit_y` au démarrage) : corriger `slit_y`, redémarrer, et voir si
      le flux augmente. Vérifier aussi la discordance dans le commentaire de
      `config72ED.yaml` (mesure KStars (614, 617) → (613, 616) annoncée, mais
      la valeur écrite est 614). Rapport : `Rosace_centre_fente_2026-09-24.pdf`
- [ ] Centre de la fente : la première image de guidage de la nuit
      (`GUIDING_RAW_HOMESYNC_10s_20260924_193401.fits`) montre la fente à
      l'œil mais `find_axes(invert=True)` au seuil 5σ ne la retient pas
      (contraste par pixel trop faible, bruit du ciel ~400 ADU). Essayer un
      seuil plus bas, ou un filtrage le long de la fente. Idéalement : une
      image de guidage dans le noir avec la lampe de calibration allumée
      (fente brillante sur fond noir, sans étoiles), à mesurer avec
      `invert=False` (2026-09-24)
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)
- [ ] Pour le calcul de foc, on peut pondérer la valeur moyenne de fWHM retenue en appliquant un coef de pondération proportionnel à la distance au centre du carré (100% au centre, 30% dans les coins). Pour durcir la valeur retenue.
- [ ] Dans `focus`, logger la température du focuser en même temps que la
      date/heure de la mesure de FWHM.
- [ ] Mettre en base de données la valeur de focus (FWHM), avec la date, la
      température, etc. — à terme, y ajouter aussi l'intensité du spectre
      une fois la bonne méthode de calcul disponible.
- [ ] Une fois le focus en base : l'afficher (GUI ou export) en fonction de
      la température du focuser, pas seulement de l'heure/date — le focus
      dépend surtout de la température, un historique chronologique seul
      ne le montre pas (2026-09-25)
- [ ] Faire un plan dobservation Be plus sophstiqué, pour ne pas refaire toujours les mêmes obserations. Prendre en compte les obserations récentes et les demandes de BeSS.
- [ ] Mettre en place un système qui permet de ne pas consommer les étoiles qui ont plantées. Peut-être calculer un facteur global de priorité
- [ ] Aller chercher toutes les Be dans BeSS. Et regarder le priorités d'observation BeSS. 

## Chantiers plus lointains

- [ ] Backend ASCOM / passage sous Windows — bloqué sur l'accès à un
      environnement Windows
- [ ] Deuxième instrument ("gros télescope", ex. UVEX) — driver
      IndiUsisSpectro existant, aucun profil matériel
- [ ] Faire tourner REDUCTION → SHARING → ARCHIVE dans un thread dédié
      (avec sa propre boucle asyncio), séparé du séquenceur : aujourd'hui
      `run_post_obs()` est une tâche de la même boucle, donc tout calcul ou
      toute écriture bloquante d'un sous-plugin de réduction fige le
      séquenceur (probablement déjà le zip d'`export_demetra`, non mesuré).
      Les sous-plugins restent séquentiels. À sécuriser :
      `state.pending_post_obs`, instances de plugins partagées, arrêt de
      session. `calculate_flux` est déjà dans `Be_stars.yaml` et bloque le
      séquenceur environ 0,5 s par pose en attendant (accepté) (2026-09-26)

## Petits items

- [ ] GUI web : l'affichage d'une nouvelle image (au moins panneau Spectre)
      repart en taille pleine à chaque nouvelle capture au lieu de garder
      le zoom courant (constaté 2026-09-19)
- [ ] Rendre le log DEBUG du fichier réellement actif (logger_spectro est
      encore en INFO : aucun DEBUG n'atteint rosace.log) — utile si le bug
      de réutilisation des darks revient
- [ ] Noirs : le premier noir de 180 s de la nuit montre une trace de
      spectre — `DARK_0deg_5x180s_20260924_193701-1.fits` (session
      2026-09-24, première image de la série, faite juste après la pose
      scientifique sur Vega). À creuser : rémanence du capteur après une
      image brillante, ou source lumineuse pas encore en position DARK au
      moment de la première pose (délai de commutation du spectroscope) ?
      Comparer avec les images 2 à 5 de la même série (2026-09-24)
- [ ] Taille des images : toutes les images de la soirée du 2026-09-24
      font 1304 × 976, contre 1391 × 1039 la nuit du 23 (même binning 1×1) —
      mêmes symptômes que l'incident du 3 septembre (sous-fenêtre
      `CCD_FRAME` restée sur le pilote Atik). À vérifier dans le panneau INDI
      de KStars ; le spectre est peut-être rogné sur les bords. Noirs, biais,
      plats et calibrations ont la même taille : leur réutilisation reste
      cohérente. Rapport : `Rosace_M57_2026-09-24.pdf` (2026-09-24)
- [ ] Rectangle plus clair dans les images scientifiques du 2026-09-24
      (environ colonnes 255-710, lignes 200-655 à l'écran, plus une ligne
      verticale vers x ≈ 710). Écart de niveau médian intérieur − extérieur :
      biais +1, noir 180 s +2, Vega (1 s) +3, Sheliak (23 s) +8, M57 (180 s)
      +12, calibration +23 ADU : lié à la lumière reçue, croît avec la pose,
      cause inconnue (lumière parasite par une ouverture rectangulaire ?).
      À vérifier sur les nuits précédentes (2026-09-24)
- [ ] Spectre de M57 (raies d'émission) : le seuil 5σ ne retient que 4 des
      9 pics du spectre en une dimension. Vérifier image par image les pics
      des colonnes 111, 363, 459 et 675 ; le pic de la colonne 1266 est un
      rayon cosmique (un pixel, ligne 559). Envisager un filtre des pixels
      isolés avant la somme des lignes — à ne faire qu'avec l'accord de
      l'utilisateur, qui veut d'abord des mesures inchangées (2026-09-24)
- [ ] `slit_xy` (2026-09-26, codé, pas encore testé sur le ciel) : première
      nuit, vérifier la ligne `[SLIT_XY]` du log (Y attendu ≈ 617 à la
      colonne 613), la surimpression zone/ligne/croix sur le panneau Guidage
      (GUI à recompiler), et que la fente est bien une ligne sombre sur
      l'image lampe CALIB à 0,1 s. Suivre ensuite le Y mesuré d'une cible à
      l'autre (flexion ?) et le résidu final de `stop_phd2_guiding`.
