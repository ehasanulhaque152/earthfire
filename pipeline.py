"""Download and prepare a provisional MODIS/VIIRS hotspot calendar.

Uses only Python's standard library. Raw FIRMS CSV is retained. Hotspot-only
data cannot establish clear-sky coverage, so zero rows are never treated as
confirmed fire-free observations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RADIUS_M = 6_371_008.8
FIELDS = [
    "source", "sensor", "satellite", "product_version", "latitude", "longitude",
    "acquired_at_utc", "date_utc", "daynight", "confidence_raw", "frp_raw",
    "scan_raw", "track_raw", "grid_x", "grid_y", "cell_id", "source_file",
]
ALLOWED_SOURCES = {"MODIS_SP", "VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"}


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    west, south, east, north = map(float, config["bbox"])
    if not (-180 <= west < east <= 180 and -90 < south < north <= 90):
        raise ValueError("bbox must be [west, south, east, north] and cannot cross the antimeridian")
    if north - south > 30 or east - west > 40 or max(abs(south), abs(north)) > 70:
        raise ValueError("This pilot grid is intended for regional areas below 70 degrees latitude")
    start, end = date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"])
    if start > end or float(config["grid_km"]) <= 0:
        raise ValueError("Check start_date, end_date, and grid_km")
    if not set(config["sources"]) <= ALLOWED_SOURCES:
        raise ValueError("Unsupported FIRMS source in config")
    if any(s.endswith("_NRT") for s in config["sources"]) and any(s.endswith("_SP") for s in config["sources"]):
        raise ValueError("Do not mix near-real-time and standard processed sources in one calendar")
    return config


def windows(start: date, end: date):
    current = start
    while current <= end:
        length = min(5, (end - current).days + 1)
        yield current, length
        current += timedelta(days=length)


def download(config: dict, base: Path) -> None:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise RuntimeError("Set FIRMS_MAP_KEY in your local environment before downloading")
    west, south, east, north = config["bbox"]
    bbox = ",".join(str(v) for v in (west, south, east, north))
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = []
    for source in config["sources"]:
        for start, length in windows(date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"])):
            target = raw / f"{source}_{start.isoformat()}_{length}.csv"
            if not target.exists():
                url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{length}/{start.isoformat()}"
                try:
                    with urllib.request.urlopen(url, timeout=120) as response:
                        body = response.read()
                except urllib.error.HTTPError as error:
                    raise RuntimeError(f"FIRMS returned HTTP {error.code} for {source}, {start}") from None
                except urllib.error.URLError as error:
                    raise RuntimeError(f"Could not reach FIRMS for {source}, {start}: {error.reason}") from None
                if not body.lstrip().startswith(b"latitude,longitude,"):
                    raise RuntimeError(f"FIRMS did not return a hotspot CSV for {source}, {start}; check key and availability")
                temp = target.with_suffix(".part")
                temp.write_bytes(body)
                temp.replace(target)
            manifest.append({"file": target.name, "source": source, "start_date": start.isoformat(), "days": length, "bytes": target.stat().st_size})
            print(f"Ready: {target.name}")
    (base / "download_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def grid_cell(lat: float, lon: float, bbox: list[float], grid_km: float) -> tuple[int, int, str]:
    """Regional spherical Lambert cylindrical equal-area grid centered on the bbox."""
    lat0 = math.radians((bbox[1] + bbox[3]) / 2)
    size = grid_km * 1000
    x = RADIUS_M * math.cos(lat0) * math.radians(lon)
    y = RADIUS_M * math.sin(math.radians(lat)) / math.cos(lat0)
    ix, iy = math.floor(x / size), math.floor(y / size)
    return ix, iy, f"{ix}_{iy}"


def parse_row(row: dict, source: str, source_file: str, config: dict) -> dict | None:
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        west, south, east, north = config["bbox"]
        if not (south <= lat <= north and west <= lon <= east):
            return None
        hhmm = str(row["acq_time"]).strip().zfill(4)
        when = datetime.strptime(row["acq_date"] + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
        if not (config["start_date"] <= when.date().isoformat() <= config["end_date"]):
            return None
        ix, iy, cell_id = grid_cell(lat, lon, config["bbox"], float(config["grid_km"]))
        sensor = "MODIS" if source.startswith("MODIS") else "VIIRS"
        return {
            "source": source, "sensor": sensor, "satellite": row.get("satellite", ""),
            "product_version": row.get("version", ""), "latitude": lat, "longitude": lon,
            "acquired_at_utc": when.isoformat().replace("+00:00", "Z"),
            "date_utc": when.date().isoformat(), "daynight": row.get("daynight", ""),
            "confidence_raw": row.get("confidence", ""), "frp_raw": row.get("frp", ""),
            "scan_raw": row.get("scan", ""), "track_raw": row.get("track", ""),
            "grid_x": ix, "grid_y": iy, "cell_id": cell_id, "source_file": source_file,
        }
    except (KeyError, ValueError) as error:
        raise ValueError(f"Bad FIRMS record in {source_file}: {error}") from error


def source_from_filename(path: Path) -> str:
    for source in sorted(ALLOWED_SOURCES, key=len, reverse=True):
        if path.name.startswith(source + "_"):
            return source
    raise ValueError(f"Cannot identify FIRMS source from {path.name}")


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare(config: dict, base: Path) -> dict:
    raw_files = sorted((base / "raw").glob("*.csv"))
    if not raw_files:
        raise RuntimeError("No CSV files in raw/. Run download or add FIRMS CSV files there.")
    normalized, seen = [], set()
    source_counts = defaultdict(int)
    for path in raw_files:
        source = source_from_filename(path)
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                item = parse_row(row, source, path.name, config)
                if item is None:
                    continue
                dedup = (source, item["satellite"], item["acquired_at_utc"], item["latitude"], item["longitude"])
                if dedup in seen:
                    continue
                seen.add(dedup)
                normalized.append(item)
                source_counts[source] += 1
    normalized.sort(key=lambda x: (x["acquired_at_utc"], x["sensor"], x["cell_id"]))
    base.mkdir(parents=True, exist_ok=True)
    write_csv(base / "normalized_hotspots.csv", FIELDS, normalized)

    grouped = defaultdict(lambda: {"cells": set(), "detections": 0})
    for item in normalized:
        key = (item["date_utc"], item["sensor"], item["daynight"])
        grouped[key]["cells"].add(item["cell_id"])
        grouped[key]["detections"] += 1
    daily = [
        {"date_utc": d, "sensor": s, "daynight": dn, "active_cells": len(v["cells"]),
         "detections": v["detections"], "coverage": "unknown_hotspots_only"}
        for (d, s, dn), v in sorted(grouped.items())
    ]
    write_csv(base / "daily_sensor_activity.csv", ["date_utc", "sensor", "daynight", "active_cells", "detections", "coverage"], daily)

    # A provisional VIIRS-referenced calibration for the selected area only.
    by_day = defaultdict(lambda: defaultdict(set))
    for item in normalized:
        by_day[(item["date_utc"], item["daynight"])][item["sensor"]].add(item["cell_id"])
    ratios = [len(v["VIIRS"]) / len(v["MODIS"]) for v in by_day.values() if v["VIIRS"] and v["MODIS"]]
    factor = statistics.median(ratios) if len(ratios) >= 14 else None
    calendar = []
    for (d, dn), values in sorted(by_day.items()):
        modis, viirs = len(values["MODIS"]), len(values["VIIRS"])
        if viirs:
            estimate, basis = viirs, "VIIRS_detected"
        elif modis and factor is not None:
            estimate, basis = round(modis * factor, 2), "MODIS_scaled_provisional"
        else:
            estimate, basis = "", "insufficient_overlap_or_coverage"
        calendar.append({"date_utc": d, "daynight": dn, "modis_active_cells": modis or "",
                         "viirs_active_cells": viirs or "", "provisional_viirs_reference": estimate,
                         "basis": basis, "coverage": "unknown_hotspots_only"})
    write_csv(base / "provisional_calendar.csv", ["date_utc", "daynight", "modis_active_cells", "viirs_active_cells", "provisional_viirs_reference", "basis", "coverage"], calendar)
    report = {
        "area": config["name"], "bbox": config["bbox"], "period": [config["start_date"], config["end_date"]],
        "grid_km": config["grid_km"], "raw_files": len(raw_files), "normalized_records": len(normalized),
        "records_by_source": dict(source_counts), "paired_sensor_daynight_bins": len(ratios),
        "provisional_viirs_to_modis_factor": factor,
        "status": "hotspot-only provisional; observation coverage and clear-sky masks not yet included",
    }
    (base / "qa_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "prepare", "all"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command in {"download", "all"}:
        download(config, args.output)
    if args.command in {"prepare", "all"}:
        print(json.dumps(prepare(config, args.output), indent=2))


if __name__ == "__main__":
    main()
