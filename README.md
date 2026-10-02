# Ensembles météo : archivage automatique

Un workflow GitHub Actions récupère 4 fois par jour les prévisions d'ensemble
(ECMWF IFS ENS, GFS/GEFS, ICON) via l'API Ensemble d'[Open-Meteo](https://open-meteo.com/en/docs/ensemble-api),
et les enregistre dans le dépôt, un fichier compressé par modèle et par exécution.

## Contenu

| Fichier | Rôle |
|---|---|
| `.github/workflows/fetch.yml` | Planification (cron), exécution, `git commit` + `git push` |
| `fetch_ensemble.py` | Appel de l'API avec retry, nettoyage, détection des doublons, écriture des snapshots |
| `config.json` | Lieux, variables, modèles, nombre de jours de prévision |
| `read_snapshots.py` | Lecture des snapshots avec pandas |
| `notebooks/ensembles_meteo_colab.ipynb` | Notebook d'exploration de l'API dans Colab |
| `data/` | Snapshots archivés |

## Mise en route

1. Créer un dépôt GitHub et y pousser ce dossier (branche par défaut, par exemple `main`).
2. Vérifier dans *Settings → Actions → General → Workflow permissions* que l'écriture est autorisée
   (`Read and write permissions`). Le workflow demande déjà `contents: write`, mais une règle d'organisation peut le restreindre.
3. Lancer une première fois à la main : onglet *Actions → Fetch ensemble forecasts → Run workflow*.
4. Les exécutions suivantes ont lieu à 00:20, 06:20, 12:20 et 18:20 UTC (soit 02:20, 08:20, 14:20 et 20:20 en heure d'été à Paris).
   GitHub peut les retarder de plusieurs minutes, voire en sauter une en cas de forte charge.

## Format des données

```
data/<lieu>/<modèle>/<AAAA>/<MM>/<AAAAMMJJTHHMMZ>.json.gz
```

Chaque fichier contient :
- `meta` : date de récupération (UTC), modèle, coordonnées demandées et de la maille du modèle, altitude,
  unités, nombre de membres, empreinte SHA-256 du contenu, mention d'attribution ;
- `hourly` : `time` (UTC) puis une colonne par variable et par membre, comme dans la réponse de l'API
  (`temperature_2m` = membre de contrôle, `temperature_2m_member01`, `..._member02`, etc.).

Si un nouvel appel renvoie exactement les mêmes données que le dernier snapshot (le modèle n'a pas publié de nouveau run),
aucun fichier n'est écrit et aucun commit n'est créé.

## Lire les données

```python
from read_snapshots import list_snapshots, load_snapshot, forecasts_for_time

paths = list_snapshots("ecmwf_ifs025")
meta, df = load_snapshot(paths[-1])        # lignes = heures UTC, colonnes = membres
forecasts_for_time("2026-10-10 12:00", "temperature_2m", "ecmwf_ifs025")
```

## Personnalisation

- **Variables** : modifier `variables` dans `config.json` (ex. `pressure_msl`, `wind_gusts_10m`, `cloud_cover`).
- **Modèles** : ajouter ou retirer des entrées dans `models` (ex. `icon_eu` avec 5 jours, `icon_d2` avec 2 jours, `gem_global`).
  Un `forecast_days` supérieur à l'horizon réel du modèle est sans danger : les heures sans données sont supprimées.
- **Lieux** : ajouter des objets dans `locations` (`name` sert de nom de dossier).
- **Fréquence** : modifier la ligne `cron` de `fetch.yml` (heures en UTC).

## Points de vigilance

- **Taille du dépôt** : chaque snapshot pèse quelques dizaines à une centaine de Ko compressé. Avec la configuration par défaut
  (3 modèles, 3 variables, 4 exécutions par jour), le dépôt grossit de l'ordre de plusieurs centaines de Ko par jour.
  Le script affiche la taille réelle de chaque fichier dans les logs : à vérifier après quelques jours,
  et à réduire si besoin (moins de variables, moins de jours, moins de modèles).
- **Inactivité** : sur un dépôt public, GitHub désactive les workflows planifiés après 60 jours sans activité.
  Faire de temps en temps un commit manuel, ou réactiver le workflow depuis l'onglet *Actions*.
- **Échecs** : si un appel échoue après plusieurs tentatives, les autres snapshots sont quand même enregistrés,
  puis le job est marqué en échec (GitHub envoie alors un e-mail).
- **Conditions d'Open-Meteo** : l'API gratuite est réservée à un usage non commercial, et les données sont sous licence
  CC BY 4.0 (attribution à Open-Meteo.com, mentionnée dans chaque snapshot). Vérifier leurs conditions en cas de republication.
