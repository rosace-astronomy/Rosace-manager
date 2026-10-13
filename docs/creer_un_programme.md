# Créer un nouveau programme d'observation dans Rosace

*Guide pour les observateurs et développeurs qui veulent ajouter leur propre programme
scientifique. Version du 3 octobre 2026 (branche `multi-program`, phases A, B1, B2 et C).*

La spécification complète du mécanisme est dans
[scheduler_multi_program_spec.md](scheduler_multi_program_spec.md) (en anglais) ; ce guide
en reprend ce qu'il faut savoir pour écrire un programme, dans l'ordre où on en a besoin.

---

## 1. Ce qu'est un programme

Un programme, c'est une question scientifique (« suivre les étoiles Be », « surveiller les
novae »…) traduite en trois choses :

| Élément | Où | Rôle |
|---|---|---|
| **Le fichier du programme** | `rosace/rosace/programs/<nom>.yaml` | Comment on observe : la suite des étapes d'acquisition (`sequencer`), de réduction (`reduction`) et de diffusion (`sharing`). Et, s'il est actif, quel planificateur décide quoi observer (`scheduler`). |
| **La liste des cibles** | base `targets.db` (table `program_stars`) | Quelles étoiles appartiennent au programme, avec éventuellement une durée de pose imposée par étoile. |
| **Le planificateur** | `rosace/rosace/plugins/program_schedulers/<nom>/<nom>.py` | Quand observer : à chaque fois que Rosace cherche la prochaine observation, il répond « voici l'étoile que j'observerais maintenant, et pourquoi », ou « rien pour l'instant ». |

Le nom du programme doit être **le même partout** : nom du fichier YAML, valeur de la clé
`program:` dans ce fichier, nom du programme dans `targets.db`. Il ne contient que des
lettres, chiffres et `_` (pas d'espace, pas de tiret) : `Be_stars`, `nova_monitoring`.

Un programme peut exister sans planificateur : il sert alors seulement à *exécuter* des
observations décidées ailleurs (par exemple par la liste manuelle, voir § 2.3).

---

## 2. Comment Rosace choisit la prochaine observation

### 2.1 Le principe

Rosace ne planifie jamais la nuit entière. Après chaque observation, le **sélecteur** (le
module SCHEDULER, activé par `active_scheduler: selector` dans `config.yaml`) :

1. demande à **chaque programme actif** une proposition (au plus une) ;
2. vérifie que chaque proposition est faisable (hauteur, horizon, méridien, fenêtre
   horaire) ;
3. choisit la plus urgente ;
4. si aucune n'est possible maintenant, attend une minute et recommence.

Chaque planificateur ne connaît que son propre programme. Le sélecteur, lui, ne connaît
rien à la science : il compare des niveaux de priorité.

### 2.2 Les niveaux de priorité — « que perd-on si on attend ? »

| Niveau | Constante | Signification |
|---|---|---|
| 0 | `PRIORITY_INTERRUPT` | L'événement lui-même, irremplaçable (nova, sursaut). Pas encore implémenté : aujourd'hui il passe seulement en premier. |
| 1 | `PRIORITY_SERIES` | Élément suivant d'une série déjà commencée par ce planificateur. |
| 2 | `PRIORITY_HIGH` | L'information est perdue si on n'observe pas cette nuit. |
| 3 | `PRIORITY_ORDINARY` | Routine : peut être rattrapé demain. |
| 4 | `PRIORITY_FILLER` | Pas nécessaire, mais mieux qu'un télescope inactif. |

Une contrainte horaire ne fixe pas le niveau : le planificateur choisit les deux
séparément. Le plus petit niveau gagne. À niveau égal, dans l'ordre :

1. au niveau 1, le programme de l'observation précédente garde la main (une série n'est
   pas interrompue par une autre série) ;
2. la liste manuelle passe avant les autres programmes ;
3. une proposition avec fenêtre horaire passe avant une proposition sans fenêtre (entre
   deux fenêtres, celle qui ferme la première) ;
4. le programme le plus en retard sur sa part de temps (§ 3.7) ;
5. l'ordre alphabétique du nom de programme.

Choisir le bon niveau est donc **la** décision scientifique d'un planificateur. Exemple
pour les Be : une étoile en phase active, à suivre chaque nuit, mérite le niveau 2 ; une
étoile dont la dernière observation date de plus que sa périodicité souhaitée, le niveau 3 ;
une étoile observée récemment mais disponible, le niveau 4.

### 2.3 Le programme minimal : la liste manuelle

`programs/manual_list.yaml` est toujours présent (sans lui le sélecteur arrête la
session). Son planificateur lit un fichier texte écrit à la main, relu à chaque appel et
modifiable pendant la nuit. Son chemin se règle dans la configuration
(`subplugin_params.observation_list_path`), pas dans `manual_list.yaml` : c'est la même
clé, donc le même fichier, que pour l'ancien planificateur `scheduler_rosace_2026`. Format :

```
Priority, Program, Star, Start_after, End_before, Series, comment
2, Be_stars, gam Cas, none, none, none, suivi
2, Be_stars, Sheliak, 22:15, 22:45, True, série calée sur une fenêtre
```

Chaque ligne nomme le programme dont le YAML exécutera l'observation : la liste manuelle
*propose*, `Be_stars` *observe*. C'est la façon la plus simple d'utiliser un nouveau
programme avant d'avoir écrit son planificateur.

---

## 3. Créer un programme, étape par étape

### 3.1 Le fichier YAML

Partir d'un programme existant (`Be_stars.yaml` pour le matériel réel) et l'adapter :

```yaml
program: mon_programme
description: "Une phrase qui dit ce que le programme étudie"

scheduler:                     # présent = programme actif ; commenter le bloc = en sommeil
  name: mon_programme          # le planificateur (dossier program_schedulers/mon_programme/)
  share_percent: 20            # facultatif : part visée du temps de télescope (§ 3.7)
  params:                      # libre : ce que votre planificateur lit dans ctx.params
    min_altitude_deg: 25
    cadence_days: 7

sequencer:                     # les étapes d'acquisition, dans l'ordre
  - slew_72ed
  - slit_xy
  - pointing_72ed
  - focus
  - star_spectra_acquisition
  - stop_phd2_guiding
  - dark_acquisition
  - bias_acquisition
  - calib_acquisition
  - flat_acquisition
  - spectro_light_source_to_sky
reduction:
  - calculate_flux
  - export_demetra
sharing: blank_share           # une seule étape de diffusion
```

Les étapes disponibles se listent avec :

```bash
python -m rosace.main --list-subplugins            # tout
python -m rosace.main --list-subplugins sequencer  # une famille
```

Les étapes propres au matériel (darks du guidage, initialisation de la monture AM3) sont
ajoutées automatiquement par le profil matériel : un programme ne les déclare jamais.

### 3.2 Les cibles dans `targets.db`

Le programme doit exister dans `targets.db`, avec ses étoiles. Tout passe par l'API REST
(interface web ou `http://localhost:8000/docs`), toujours par les noms :

- `POST /programs` : créer le programme (même nom que le YAML) ;
- `POST /stars/import-list` : importer des étoiles depuis un fichier texte, un nom par
  ligne ; chaque nom est résolu par Simbad (coordonnées J2000, magnitude V, type
  spectral) et gardé comme alias si ce n'est pas le nom canonique Simbad ;
- `POST /programs/import-list` : relier des étoiles existantes au programme, fichier YAML
  `{mon_programme: [étoile, étoile, ...]}`.

Une étoile peut appartenir à plusieurs programmes. Pour chaque couple (programme, étoile),
`program_stars.exposure_time` peut imposer la durée d'**une** pose élémentaire ; sinon elle
est calculée depuis la magnitude (`exposure_time_mag0_seconds × 10^(mag/2,5)`), découpée en
poses d'au plus `max_exposure_time_seconds`, au moins `min_exposure_count` poses.

> **Attention aux étoiles faibles.** Avec le réglage actuel du 72ED (4 s à magnitude 0),
> le temps de pose total calculé est d'environ 17 min à magnitude 6, 1 h 45 à magnitude 8
> et 11 h à magnitude 10. Les vérifications communes refusent une observation qui ne tient pas
> au-dessus de l'horizon : une étoile trop faible ne sera tout simplement jamais proposée.
> Il faut alors fixer `exposure_time` pour ces étoiles, ou que le planificateur décide
> lui-même de la durée.

### 3.3 Le planificateur

1. Copier `plugins/program_schedulers/blank_program/` en
   `plugins/program_schedulers/mon_programme/`, renommer le fichier en
   `mon_programme.py` et la classe en `MonProgrammeScheduler` (nom en snake_case → PascalCase
   + `Scheduler`). Aucun registre à modifier : le nom dans le YAML suffit.
2. Lire la docstring de `blank_program.py` : c'est le guide de référence du développeur,
   en anglais. Ce qui suit en est le résumé.

Le planificateur n'a qu'une méthode :

```python
async def propose(self, ctx: SchedulingContext) -> Candidate | None
```

**Ce qu'il reçoit** (`ctx`) :

| Champ | Contenu |
|---|---|
| `now` | Instant UTC de la décision, le même pour tous les planificateurs. **Toujours l'utiliser** plutôt que lire l'horloge (les tests simulent n'importe quelle heure). |
| `site` | `latitude_deg`, `longitude_deg` (est positif), `elevation_m`. |
| `program` | Le nom du programme dont le YAML déclare ce planificateur. |
| `params` | Le bloc `params` du YAML. Lire chaque clé avec sa valeur par défaut : `ctx.params.get("cadence_days", 7)`. |

**Ce qu'il rend** : `None` si rien n'est à faire maintenant (ce n'est pas une erreur), sinon
un `Candidate` :

| Champ | Contenu |
|---|---|
| `queried_name` | Nom de l'étoile, canonique ou alias ; le sélecteur la cherche dans `targets.db` (inconnue → refusée). |
| `program` | Programme dont le YAML exécutera l'observation (en général `ctx.program`). |
| `priority` | 0 à 4 (§ 2.2). |
| `exposure` | `ExposureConfig(exposure_time=...)` : durée d'une pose élémentaire si connue, sinon `None` (calcul par la magnitude au moment de l'acquisition). |
| `window` | `TimeWindow(start_after=..., end_before=...)` en UTC, ou `None` (§ 3.5). |
| `reason` | Une phrase lisible : pourquoi cette cible maintenant (« dernier succès il y a 19 jours, périodicité 15 jours »). Elle va dans le journal et dans le dossier de l'observation. |

### 3.4 Les règles à respecter

- **Répondre vite** : en moins de `planner_timeout_seconds` (10 s). Chaque planificateur
  tourne dans son propre fil d'exécution : s'il est lent ou bloqué, il perd seulement son
  tour, Rosace continue.
- **Ne proposer que du faisable**, avec les vérifications communes (ci-dessous), les mêmes
  que le sélecteur refera : un refus par le sélecteur d'une proposition que le
  planificateur croyait faisable est un bug du planificateur.
- **Ne garder aucun état** entre deux appels : tout ce dont il a besoin est dans
  `rosace.db`. Un redémarrage du serveur ne fait alors rien perdre.
  `database.db_get_successes_for_target(program, star, ctx.now)` donne le nombre
  d'observations réussies cette nuit et l'heure de la dernière ;
  `database.db_get_last_success_per_target(program)` la date de la dernière observation
  réussie de chaque cible, toutes nuits confondues (pour une cadence).
- **Lecture seule** : ne jamais écrire dans `targets.db`, `rosace.db` ni aucun fichier de
  l'installation, ne jamais toucher au matériel. Une exception : un planificateur peut
  tenir à jour sa propre copie d'un fichier de données externe, sous
  `~/.rosace/<programme>/`, avec un accès réseau borné (bien en dessous des 10 s) et sans
  jamais en dépendre pour répondre — c'est ce que fait `be_scheduler` avec le fichier
  Robot de BeSS.
- **Vos critères scientifiques sont les vôtres** (hauteur minimale, Lune, angle horaire
  préféré, cadence…) : les appliquer avant de choisir.
- Une seule proposition : parmi vos cibles possibles, c'est à vous de choisir la meilleure.

**Les vérifications communes** (`program_schedulers/checks.py`) :

| Fonction | Rend |
|---|---|
| `check_feasible(star, exposure, ctx.now, min_altitude_deg)` | `None` si faisable, sinon la raison : hauteur au-dessus du plancher de l'installation (et de votre propre minimum) et de l'horizon local, vérifiée toutes les 5 minutes sur toute la durée de l'observation, plus les règles de la monture : pas de départ trop près du méridien (`min_meridian_distance_deg`), pas de suivi plus loin au-delà du méridien que la monture ne le permet (`meridian_tolerance_deg`). |
| `await check_not_recently_failed(program, star.name, ctx.now)` | `None`, ou un refus si la cible a échoué il y a moins de `failure_backoff_hours` (1 h) pour ce programme. |
| `estimate_duration_s(star, exposure)` | La durée estimée en secondes (poses + temps fixe `overhead_minutes`). |

**Erreurs.** Une cible inutilisable : la journaliser et passer à la suivante. Une
exception : le programme est ignoré pour ce tour seulement. `SchedulerFatalError`
(`plugins/schedulers`) : problème de configuration ou de données qu'un nouvel essai ne
corrigera pas ; le programme est désactivé jusqu'à la fin de la session.

### 3.5 Fenêtres horaires et observations réservées

Une fenêtre `TimeWindow` dit quand l'observation doit avoir lieu :

- `start_after` : début des **poses scientifiques** ; l'observation est lancée
  `overhead_minutes` plus tôt (pointage, calibrations) — `checks.launch_time(window)` ;
- `end_before` : fin de **toute** l'observation.

Avant de proposer une observation avec fenêtre, appeler
`checks.check_window(window, ctx.now, durée)` :

- `"open"` : faisable maintenant ;
- `"booked"` (réservée) : le lancement est encore à venir, dans moins de
  `lookahead_minutes` (60 min) ; vérifier la faisabilité à partir de ce moment
  (`check_feasible(..., start=launch_time(window))`). Le sélecteur ne lance jamais une
  réservation en avance ; il protège son créneau en écartant les observations moins
  urgentes qui déborderaient dessus ;
- `"refused"` : fenêtre fermée, trop courte, ou trop lointaine — ne pas proposer.

Tant que votre proposition est réservée, vous ne proposez rien d'autre (sauf une
observation qui se termine avant son lancement, si vous voulez remplir le temps).

### 3.6 Les séries

Une série, c'est observer une même cible en continu, sans rien entre deux éléments. Le
sélecteur ne sait rien des séries : le planificateur fait tout.

- Avant le premier élément, vérifier la série entière :
  `check_feasible(..., duration_s=durée_totale_de_la_série)`.
- Proposer le premier élément à son niveau normal (2, 3 ou 4), puis chaque élément suivant
  au niveau 1, tant que la dernière observation de la nuit est la vôtre
  (`db_get_recent_observations(limit=1)`) et que la série n'est pas finie.
- Le délai après échec ne s'applique pas au niveau 1 : un élément raté est retenté tout de
  suite. Bornez vous-même les essais (`database.db_count_trailing_failures()` : nombre
  d'échecs consécutifs ; la liste manuelle s'arrête à 3).
- Un retournement de la monture entre deux éléments est accepté.

### 3.7 Les parts de temps

`share_percent` (facultatif, dans le bloc `scheduler`) fixe la part du temps de télescope
visée par le programme. Le temps compté est la durée réelle de chaque observation, échecs
compris, sur 60 jours glissants. La part ne sert **qu'à départager** deux propositions de
même niveau : le programme le plus en retard passe. La somme des parts de tous les
programmes ne doit pas dépasser 100 % (la session s'arrête sinon, avec un message clair) ;
la liste manuelle n'a pas de part, ses observations comptent pour le programme qu'elles
exécutent.

### 3.8 Les tests

Écrire `plugins/program_schedulers/mon_programme/test_mon_programme.py`, sur le modèle de
`blank_program/test_blank_program.py` : contexte `SchedulingContext` construit à la main,
calcul de hauteur remplacé par des valeurs fixes (`monkeypatch` sur `checks.altitude_deg`),
historique écrit dans `rosace.db` comme de vraies observations. Vérifier au minimum :

- `None` quand il n'y a rien à faire ;
- un `Candidate` valide sinon (niveau 0–4, programme et raison renseignés) ;
- chacune de vos propres règles (cadence, hauteur, choix entre deux cibles…).

```bash
cd rosace
pytest rosace/plugins/program_schedulers -v
```

### 3.9 Activer le programme

1. `active_scheduler: selector` dans `config.yaml` (revenir en arrière tient en une ligne :
   `active_scheduler: scheduler_rosace_2026`).
2. Le bloc `scheduler:` présent dans le YAML du programme : il est actif dès le prochain
   démarrage de session. Le commenter le met en sommeil.
3. Vérifier la configuration sans lancer de session :
   `python -m rosace.main --check-config`.
4. Au premier tour, le journal affiche une ligne par tour de sélection : qui a proposé
   quoi, ce qui a été refusé et pourquoi, ce qui a été choisi.

---

## 4. Exemples

**L'exemple réel le plus simple** : `plugins/program_schedulers/random_pick/`, partagé par
les programmes `PN` et `Cataclysmic`. Il tire au hasard une cible du programme pas encore
observée cette nuit et faisable, et la propose au niveau 3. Une trentaine de lignes.

**Un exemple réel complet** : `plugins/program_schedulers/be_scheduler/` (programme `Be_stars`).
Il observe le plus régulièrement possible les étoiles Be plus brillantes qu'une magnitude
limite : dernière observation = la plus récente entre la base BeSS (fichier Robot,
téléchargé chaque nuit) et Rosace ; niveau 2 si l'étoile est en retard et que BeSS demande
une période plus courte que celle du programme, 3 si elle est en retard, 4 sinon ; tri par
retard, puis hauteur dans le ciel, puis éclat. Sa docstring décrit tout le fonctionnement.

**Un squelette** à compléter avec vos propres critères :

Il propose, au niveau 3, l'étoile la plus haute de son programme qui n'a pas
encore été observée avec succès cette nuit.

```python
"""
Example planner — proposes the highest star of its program not yet observed
successfully tonight.
"""

import logging

from ....astro_math import altitude_deg
from ....database import db_get_successes_for_target
from ....models import ExposureConfig
from ....targets import program_get_by_name, program_get_stars
from .. import PRIORITY_ORDINARY, Candidate, SchedulingContext
from ..checks import check_feasible, check_not_recently_failed

log = logging.getLogger("spectro.plugin.planner.mon_programme")


class MonProgrammeScheduler:

    async def propose(self, ctx: SchedulingContext) -> Candidate | None:
        program = await program_get_by_name(ctx.program)
        if program is None:
            log.warning(f"[SCHEDULER] Program '{ctx.program}' not found in targets.db")
            return None

        min_altitude_deg = ctx.params.get("min_altitude_deg")
        best = None
        for star in await program_get_stars(program.id):
            successes, _ = await db_get_successes_for_target(
                program.name, star.name, ctx.now)
            if successes > 0:
                continue
            if await check_not_recently_failed(
                    program.name, star.name, ctx.now) is not None:
                continue
            exposure = ExposureConfig(exposure_time=star.exposure_time)
            if check_feasible(star, exposure, ctx.now, min_altitude_deg) is not None:
                continue
            altitude = altitude_deg(star.ra, star.dec, ctx.site.latitude_deg,
                                    ctx.site.longitude_deg, ctx.now)
            if best is None or altitude > best[0]:
                best = (altitude, star, exposure)

        if best is None:
            return None
        altitude, star, exposure = best
        return Candidate(
            queried_name=star.name,
            program=program.name,
            priority=PRIORITY_ORDINARY,
            exposure=exposure,
            reason=f"not yet observed tonight, highest available ({altitude:.0f} deg)",
        )
```

Le code, les commentaires et les messages du journal sont en anglais (règle du projet).

---

## 5. Liste de contrôle

- [ ] Un nom unique, lettres/chiffres/`_`, identique dans le YAML, `program:` et `targets.db`.
- [ ] `programs/<nom>.yaml` avec `sequencer`, `reduction`, `sharing`, et un bloc
      `scheduler` pour être actif.
- [ ] Le programme et ses étoiles dans `targets.db` ; durée de pose fixée pour les étoiles
      trop faibles.
- [ ] `plugins/program_schedulers/<nom>/<nom>.py`, classe `<Nom>Scheduler`, méthode
      `propose()`.
- [ ] Seulement des propositions vérifiées par `check_feasible()` et
      `check_not_recently_failed()` (et `check_window()` avec une fenêtre).
- [ ] Aucun état gardé, aucune écriture, `ctx.now` plutôt que l'horloge.
- [ ] Une raison lisible pour chaque proposition.
- [ ] `test_<nom>.py` qui passe.
- [ ] `share_percent` cohérent avec les autres programmes (total ≤ 100 %).
- [ ] Essai sur les simulateurs INDI avant une vraie nuit.
