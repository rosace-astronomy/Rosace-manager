# TODO

_Dernière mise à jour : 2026-09-19_

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] **URGENT** — bêta CMi saturé (spectre) : le temps de pose à mag0
      (`exposure_time_mag0_seconds`) est probablement trop long pour les
      étoiles brillantes du programme — revoir le calcul pour bêta CMi et
      vérifier les autres cibles brillantes du plan d'observation
      (constaté 2026-09-20)
- [ ] **URGENT** — Recalibrer les temps de pose une fois le correctif
      gain/offset de la caméra de guidage en place (2026-09-20) : elle
      tournait avec gain=0/offset=0 (jamais configurés par Rosace,
      confirmé dans KStars), ce qui faussait sa sensibilité réelle ;
      passer à gain=50/offset=20 (valeurs confirmées par l'utilisateur en
      direct dans KStars) va changer significativement la réponse de la
      caméra — revoir `subplugin_params.guiding.exposure_seconds` (10s
      aujourd'hui) et tout temps de pose calibré empiriquement en
      supposant l'ancienne sensibilité
- [ ] **URGENT** — Focuser qui ne répond pas bien en descente (commandé
      42100 → 42000 → 41500 → 41000, n'atteint pas les positions cibles,
      comme un point dur mécanique) ; remontée à 41500 semblait OK
      (constaté 2026-09-20, à distance, pendant une mise au point
      manuelle du guidage) — reboot du Stellarmate fait dans la foulée
      pour voir si ça se résout ; à vérifier sur place (effort moteur,
      câblage, température) si ça persiste après le reboot
- [ ] **PRIORITÉ FORTE** — Reconnexion PHD2 après un reconnect INDI de la
      caméra de guidage (ne suit pas automatiquement aujourd'hui) : 3ᵉ
      occurrence confirmée le 2026-09-19, a fait planter Sheliak, zêta Oph
      et omi And d'affilée (PHD2 refuse de guider avec "all equipment must
      be connected first" tant qu'il n'est pas reconnecté à la main) —
      mêmes symptômes que les incidents du 2026-09-11 et du 2026-09-18
      (voir mémoire projet USB). Cette fois, aucune trace de déconnexion/
      reconnexion INDI visible dans le log de Rosace (contrairement au
      18/09) — la cause côté caméra reste donc incertaine, pas forcément
      un problème USB classique cette fois. Deux problèmes liés mais
      distincts : (1) pourquoi PHD2 perd son équipement, (2) pourquoi il
      ne récupère pas tout seul après
- [ ] Aucune vérification que l'étoile est réellement dans la fente avant
      de poursuivre : `pointing_72ed` (résidu Phase 2) et
      `stop_phd2_guiding` (résidu post-science, `residual_arcsec`) ne font
      que loguer/enregistrer le résidu, sans seuil ni rejet — une
      observation peut donc aller jusqu'à l'archivage avec un spectre sans
      trace spectrale (fond/bias normal, mais étoile hors fente donc aucune
      lumière stellaire entrée dans le spectro — ex. verrouillage sur un
      reflet, 2026-09-20 ; sans rapport avec le souci de dark de guidage
      saturé du 2026-09-19, qui lui donnait une image quasi à zéro) sans
      qu'aucune alerte ne soit levée ; envisager un seuil sur le résidu
      (Phase 2 et/ou post-science) qui échoue ou au moins signale
      clairement l'observation
- [ ] Mise au point de la caméra de guidage qui dérive en cours de nuit :
      identifiée comme cause probable de la quasi-totalité des échecs de
      la nuit du 2026-09-19 (timeout de plate-solve Phase 1 systématique
      de 20h53 à 3h02 sur toutes les cibles restantes, indépendamment de
      PHD2 — voir mémoire) ; effet de bord observé le 2026-09-20 : étoile
      guide verrouillée sur un reflet au lieu d'une vraie étoile (zêta Tau
      hors fente). Le focuser lui-même montre un défaut mécanique côté
      guidage (point dur en descente, cf. item câble/port USB plus bas —
      pas forcément lié). Pas de mise au point logicielle sur cette caméra
      aujourd'hui — ajouter une étape qui vérifie la netteté (au moins en
      début de nuit) et alerte avant de lancer une nuit entière
      d'observations ratées. Ne pas réinventer la détection d'étoiles :
      utiliser `photutils` (package affilié à Astropy, pas encore une
      dépendance du projet aujourd'hui, seuls `astropy`/`numpy` le sont) —
      `DAOStarFinder`/`IRAFStarFinder` détecte les sources en éliminant
      déjà les pixels chauds/rayons cosmiques (forme non stellaire,
      colonnes `sharpness`/`roundness`), puis ajustement de profil
      (`photutils.psf` ou `astropy.modeling.Gaussian2D`) pour mesurer la
      FWHM de plusieurs étoiles du champ (ou d'une zone) et en prendre la
      médiane — approche standard (SExtractor/PixInsight/N.I.N.A. tous
      pareils), pas une bidouille sur une seule étoile. `SEP`/`sep-pjw`
      (plus rapide que photutils en pur Python) et `astroscrappy`
      (LACosmic, rejet de rayons cosmiques) sont des compléments possibles
      si besoin, pas indispensables au départ. Point important à ne pas
      manquer à l'implémentation : ni `DAOStarFinder`/`IRAFStarFinder` ni
      le HFD "à la main" n'excluent automatiquement les étoiles saturées
      (attendu avec 10s de pose) — une étoile saturée fausse la FWHM/HFD
      mesurée (profil aplati) ; il faut explicitement masquer les pixels
      ≥ seuil de saturation de la caméra et/ou écarter du calcul toute
      étoile dont la vignette contient un pixel saturé, avant d'agréger
      sur plusieurs étoiles. Complémentaire à l'item
      plus haut sur la vérification du résidu étoile-vs-fente : l'un
      prévient le défaut, l'autre détecte le résultat
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
- [ ] **URGENT/PRIORITÉ FORTE** — Durcir la gestion des déconnexions USB
      (flag de déconnexion sur IndiClient.removeDevice(), timeout par
      étape SEQUENCER, arrêt de session au lieu de relance sur
      déconnexion en cours d'observation) — conception actée 2026-09-04,
      jamais implémentée. Concerne la caméra de guidage/PHD2 (voir
      l'item ci-dessus) : un seul appareil en échec bloque tout
      INIT_SESSION même s'il ne sert à rien à cet instant, et rien ne
      gère proprement une déconnexion en cours de session
- [ ] Confirmer/écarter la cause du bug de réutilisation dark/bias/calib/flat
      (log DEBUG frame_size en place depuis le 2026-09-18)
- [ ] `guiding_dark_acquisition` n'a aucun contrôle de fraîcheur/qualité :
      un dark de guidage pris de jour (ciel encore lumineux) ressort saturé
      et, une fois soustrait, rend toutes les images de guidage suivantes
      quasi noires dès l'étape "home" (constaté 2026-09-19, résolu en
      supprimant le dossier de session et en relançant après la nuit
      tombée) — réfléchir à un garde-fou (détection de saturation/niveau de
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
      localement tant que le driver n'a pas confirmé) ; les relais du SM Pro
      sont commandés en boucle ouverte, sans aucun retour de position
      exploitable (contrairement à la monture et sa vérification par
      relecture de coordonnées) — donc pas de vérification possible, une
      temporisation fixe après le changement de position (~1s ?) est la
      seule piste envisagée pour l'instant. Pas implémenté : observations
      en cours au moment du constat, ne pas modifier le code pendant une
      session ; à confirmer sur une prochaine nuit (note : dans une même
      session, dark_acquisition ne rejoue pas le changement de position
      pour les observations suivantes tant que la série du jour est
      réutilisée — pas de nouvelle donnée attendue avant une nouvelle
      série fraîche)
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

- [X] Masque d'horizon local (az-dépendant) — bloqué sur l'export de
      l'horizon KStars du PC de bureau
- [X] Filtre anti-retournement au méridien (~3° proposé, pas codé)
- [ ] Distance à la Lune : filtre scheduler sur la distance de la cible à
      la Lune (seuil à définir, probablement selon la phase) — pas urgent
- [ ] Journalisation temporelle des étapes (time-series DB type InfluxDB)
- [ ] Supprimer le sub-plugin `focus` placeholder — différé jusqu'à la
      fin de la période de test sur le ciel
- [ ] Pipeline de réduction natif Rosace — bloqué sur des exemples XML
      Demetra
- [ ] Calcul du centre de la fente.
- [ ] Responsive design sur la GUI web (X4) pour tablette et téléphone
      (2026-09-20)
- [ ] Revoir les bibliothèques "fragiles"/peu maintenues du projet — une ou
      deux repérées, probablement liées au chantier ASCOM/Windows (à
      confirmer/préciser lesquelles) (2026-09-20)

## Chantiers plus lointains

- [ ] Backend ASCOM / passage sous Windows — bloqué sur l'accès à un
      environnement Windows
- [ ] Deuxième instrument ("gros télescope", ex. UVEX) — driver
      IndiUsisSpectro existant, aucun profil matériel

## Petits items

- [ ] Mesure spectre-vs-position-fente
- [ ] GUI web : afficher les coordonnées (x, y) et l'intensité du pixel
      sous le curseur dans les panneaux d'image (Spectre / Guidage)
- [ ] GUI web : l'affichage d'une nouvelle image (au moins panneau Spectre)
      repart en taille pleine à chaque nouvelle capture au lieu de garder
      le zoom courant (constaté 2026-09-19)
- [ ] Rendre le log DEBUG du fichier réellement actif (logger_spectro est
      encore en INFO : aucun DEBUG n'atteint rosace.log) — utile si le bug
      de réutilisation des darks revient
- [ ] Décider si CLOSE_SESSION doit attendre la fin du refroidissement de la
      caméra (erreurs "Failed to set temperature" du driver Atik après un
      arrêt en plein refroidissement)
- [ ] Entrée HISTORY.md du 2026-09-19 : faux "Target reached" du
      refroidissement, masque d'horizon local, convention de pixel
- [X] Repasser rosace.log de DEBUG à INFO une fois le développement actif
      AM3/pointage terminé
