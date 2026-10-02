#!/usr/bin/env python3
"""Archive les prévisions d'ensemble Open-Meteo.

Pour chaque lieu et chaque modèle de config.json, appelle l'API Ensemble et écrit
un snapshot compressé :

    data/<lieu>/<modèle>/<AAAA>/<MM>/<AAAAMMJJTHHMMZ>.json.gz

Si les données sont identiques à celles du dernier snapshot (même run du modèle),
rien n'est écrit. Code de sortie : 0 si tout s'est bien passé, 1 si au moins un
appel a échoué (les autres snapshots sont quand même écrits).
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ATTRIBUTION = "Weather data by Open-Meteo.com (https://open-meteo.com/), CC BY 4.0"
RETRY_DELAYS = (5, 20, 60, 120)  # secondes d'attente avant chaque nouvelle tentative
ROOT = Path(__file__).resolve().parent


def log(msg):
    print(msg, flush=True)


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("locations", "variables", "models"):
        if not cfg.get(key):
            raise SystemExit(f"config.json : la clé '{key}' est absente ou vide")
    return cfg


def request_with_retry(params):
    """GET avec reprise sur 429, 5xx et erreurs réseau. Les autres 4xx échouent tout de suite."""
    last_error = "inconnue"
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            r = requests.get(
                API_URL,
                params=params,
                timeout=90,
                headers={"User-Agent": "ensembles-meteo-archiver (GitHub Actions)"},
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                last_error = f"HTTP {r.status_code}"
            else:
                try:
                    reason = r.json().get("reason", r.text[:200])
                except ValueError:
                    reason = r.text[:200]
                raise RuntimeError(f"HTTP {r.status_code} : {reason}")
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = repr(exc)
        if attempt < len(RETRY_DELAYS):
            wait = RETRY_DELAYS[attempt]
            log(f"  nouvelle tentative dans {wait}s ({last_error})")
            time.sleep(wait)
    raise RuntimeError(f"échec après {len(RETRY_DELAYS) + 1} tentatives : {last_error}")


def clean_hourly(hourly):
    """Retire les colonnes entièrement nulles et les heures finales sans aucune valeur."""
    if "time" not in hourly:
        raise RuntimeError("réponse sans colonne 'time'")
    times = hourly["time"]
    cols = {
        k: v for k, v in hourly.items()
        if k != "time" and any(x is not None for x in v)
    }
    if not cols:
        raise RuntimeError("toutes les valeurs sont nulles")
    last = max(max(i for i, x in enumerate(v) if x is not None) for v in cols.values())
    n = last + 1
    out = {"time": times[:n]}
    out.update({k: v[:n] for k, v in cols.items()})
    return out


def count_members(columns, variable):
    return sum(1 for c in columns if c == variable or c.startswith(variable + "_member"))


def content_hash(hourly):
    blob = json.dumps(hourly, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def latest_snapshot(model_dir):
    files = sorted(model_dir.glob("*/*/*.json.gz"))
    return files[-1] if files else None


def read_hash(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)["meta"]["content_sha256"]
    except Exception:
        return None


def write_snapshot(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(raw)


def process(loc, model, variables, out_root, now):
    """Retourne (chemin écrit ou None, message)."""
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "hourly": ",".join(variables),
        "models": model["id"],
        "forecast_days": model["forecast_days"],
        "timezone": "GMT",
    }
    data = request_with_retry(params)
    if "hourly" not in data:
        raise RuntimeError("réponse sans bloc 'hourly'")
    hourly = clean_hourly(data["hourly"])
    digest = content_hash(hourly)

    model_dir = out_root / loc["name"] / model["id"]
    last = latest_snapshot(model_dir)
    if last is not None and read_hash(last) == digest:
        return None, "inchangé (même run que le dernier snapshot)"

    columns = [c for c in hourly if c != "time"]
    n_members = next(
        (n for n in (count_members(columns, v) for v in variables) if n > 0), 0
    )
    units = {k: v for k, v in data.get("hourly_units", {}).items() if k in hourly}
    payload = {
        "meta": {
            "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": API_URL,
            "attribution": ATTRIBUTION,
            "model": model["id"],
            "location": loc["name"],
            "requested_latitude": loc["latitude"],
            "requested_longitude": loc["longitude"],
            "grid_latitude": data.get("latitude"),
            "grid_longitude": data.get("longitude"),
            "elevation": data.get("elevation"),
            "timezone": "GMT",
            "forecast_days_requested": model["forecast_days"],
            "n_hours": len(hourly["time"]),
            "n_members_per_variable": n_members,
            "hourly_units": units,
            "content_sha256": digest,
        },
        "hourly": hourly,
    }
    path = (
        model_dir
        / now.strftime("%Y")
        / now.strftime("%m")
        / f"{now.strftime('%Y%m%dT%H%MZ')}.json.gz"
    )
    write_snapshot(path, payload)
    size_kb = path.stat().st_size / 1024
    return path, f"écrit {path.name} ({size_kb:.0f} Ko, {n_members} membres, {len(hourly['time'])} h)"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    out_root = ROOT / cfg.get("output_dir", "data")
    now = datetime.now(timezone.utc)

    lines, failures, written = [], [], 0
    for loc in cfg["locations"]:
        for model in cfg["models"]:
            label = f"{loc['name']}/{model['id']}"
            try:
                path, msg = process(loc, model, cfg["variables"], out_root, now)
                written += path is not None
                log(f"[{label}] {msg}")
                lines.append(f"- `{label}` : {msg}")
            except Exception as exc:  # on continue avec les autres modèles
                failures.append(label)
                log(f"[{label}] ERREUR : {exc}")
                lines.append(f"- `{label}` : **ERREUR** {exc}")

    log(f"Terminé : {written} snapshot(s) écrit(s), {len(failures)} échec(s).")

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"### Ensembles Open-Meteo - {now:%Y-%m-%d %H:%M} UTC\n" + "\n".join(lines) + "\n")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
