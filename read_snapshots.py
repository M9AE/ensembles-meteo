"""Lecture des snapshots archivés par fetch_ensemble.py (nécessite pandas).

Exemple (dans Colab, après avoir cloné le dépôt) :

    from read_snapshots import list_snapshots, load_snapshot, forecasts_for_time
    paths = list_snapshots("ecmwf_ifs025")
    meta, df = load_snapshot(paths[-1])          # dernier run : lignes = heures, colonnes = membres
    rev = forecasts_for_time("2026-10-10 12:00", "temperature_2m", "ecmwf_ifs025")
    rev.median(axis=1).plot()                    # évolution de la médiane d'un run à l'autre
"""

import gzip
import json
from pathlib import Path

import pandas as pd


def list_snapshots(model, location="paris", data_dir="data"):
    """Chemins des snapshots d'un modèle, du plus ancien au plus récent."""
    return sorted(Path(data_dir, location, model).glob("*/*/*.json.gz"))


def load_snapshot(path):
    """Retourne (meta, DataFrame indexé par l'heure UTC, une colonne par membre et variable)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return payload["meta"], df.set_index("time")


def member_columns(df, variable):
    """Colonnes du contrôle et des membres pour une variable."""
    return [c for c in df.columns if c == variable or c.startswith(variable + "_member")]


def forecasts_for_time(target, variable, model, location="paris", data_dir="data"):
    """Toutes les prévisions faites pour un instant cible (UTC).

    Lignes = date de récupération du snapshot, colonnes = membres.
    Permet de voir comment la prévision (et son incertitude) évolue d'un run à l'autre.
    """
    target = pd.Timestamp(target)
    target = target.tz_localize("UTC") if target.tzinfo is None else target.tz_convert("UTC")
    rows = {}
    for path in list_snapshots(model, location, data_dir):
        meta, df = load_snapshot(path)
        if target in df.index:
            rows[pd.Timestamp(meta["fetched_at"])] = df.loc[target, member_columns(df, variable)]
    return pd.DataFrame(rows).T.sort_index()
