# TODO

## Dernière mise à jour : 2026-09-21 07:29

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

## Chantiers proposés

- [ ] Distance à la Lune : filtre scheduler sur la distance de la cible à
      la Lune (seuil à définir, probablement selon la phase) — pas urgent
- [ ] Journalisation temporelle des étapes (time-series DB type InfluxDB)
- [ ] Pipeline de réduction natif Rosace — bloqué sur des exemples XML
      Demetra
- [ ] Calcul du centre de la fente.
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)
- [ ] Pour le calcul de foc, on peut pondérer la valeur moyenne de fWHM retenue en appliquant un coef de pondération proportionnel à la distance au centre du carré (100% au centre, 30% dans les coins). Pour durcir la valeur retenue.

## Chantiers plus lointains

- [ ] Backend ASCOM / passage sous Windows — bloqué sur l'accès à un
      environnement Windows
- [ ] Deuxième instrument ("gros télescope", ex. UVEX) — driver
      IndiUsisSpectro existant, aucun profil matériel

## Petits items

- [ ] GUI web : l'affichage d'une nouvelle image (au moins panneau Spectre)
      repart en taille pleine à chaque nouvelle capture au lieu de garder
      le zoom courant (constaté 2026-09-19)
- [ ] Rendre le log DEBUG du fichier réellement actif (logger_spectro est
      encore en INFO : aucun DEBUG n'atteint rosace.log) — utile si le bug
      de réutilisation des darks revient
