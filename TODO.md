# TODO

_Dernière mise à jour : 2026-09-19_

Liste des tâches en attente du projet Rosace. Claude peut ajouter une
ligne dès qu'une nouvelle idée apparaît en conversation. **Seul
l'utilisateur supprime une ligne ou la marque `[x]` (terminée)** —
Claude peut le suggérer, jamais le faire lui-même.

## Bugs / fiabilité matérielle

- [ ] Durcir la gestion des déconnexions USB (flag de déconnexion sur
      IndiClient.removeDevice(), timeout par étape SEQUENCER, arrêt de
      session au lieu de relance sur déconnexion en cours d'observation)
      — conception actée 2026-09-04, jamais implémentée
- [ ] Reconnexion PHD2 après un reconnect INDI (ne suit pas
      automatiquement aujourd'hui)
- [ ] Confirmer/écarter la cause du bug de réutilisation dark/bias/calib/flat
      (log DEBUG frame_size en place depuis le 2026-09-18)
- [ ] Pointage près du pôle : _slew_with_backlash_approach() envoie
      dec + backlash_offset_deg, ce qui dépasse 90° pour une cible de
      déclinaison > 88° (ex. Polaris, 89,3°) — comportement de la monture
      non vérifié, probablement un bug

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

## Chantiers plus lointains

- [ ] Backend ASCOM / passage sous Windows — bloqué sur l'accès à un
      environnement Windows
- [ ] Deuxième instrument ("gros télescope", ex. UVEX) — driver
      IndiUsisSpectro existant, aucun profil matériel

## Petits items

- [ ] Mesure spectre-vs-position-fente
- [ ] GUI web : afficher les coordonnées (x, y) et l'intensité du pixel
      sous le curseur dans les panneaux d'image (Spectre / Guidage)
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
