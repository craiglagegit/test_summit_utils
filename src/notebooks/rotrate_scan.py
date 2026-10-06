"""Scan the rotator-rate (rotRate) model error as a function of Az / El.

The DM-56137 rotator false positives are driven by a ~1% error in the
closed-form ``rotRate`` computed in ``getLinearRates`` (mountAnalysis.py):

    rotRate = -EARTH_ROTATION * cos(lat) * cos(az) / cos(el) / 3600

This script samples visits spread over the Az/El plane, and for each one
compares the *actual* rotator rate (from the telemetry ``actualPosition``)
against the *model* rate (from the fitted ``linearModel``), exactly as in the
user's snippet:

    actualRate = (rotValues[-1]  - rotValues[0])  / dT
    modelRate  = (rotModelValues[-1] - rotModelValues[0]) / dT
    percentError = (actualRate - modelRate) / actualRate * 100

The ``dT`` cancels in the ratio, so it does not need to be exact. Results
(az, el, rates, percentError) are written to a CSV for plotting.
"""

from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

DEFAULT_PARQUET_DIR = (
    "/sdf/home/c/cslage/claude/claude-scons_db/src/claude_scons_db/parquet_files"
)
DEFAULT_OUT = "/sdf/home/c/cslage/claude/test_summit_utils/results_rotrate/rotrate_scan.csv"

CORRUPTED_FILES = {"nightly_aos_table_20260619_summit.parquet"}

logger = logging.getLogger("rotrate_scan")

FIELDS = [
    "visit_id", "night", "azimuth", "elevation", "zenith_angle",
    "actualRate", "modelRate", "percentError", "rotRms", "imageImpactRms", "status",
]


def select_visits(parquet_dir: str, n_az_bins: int, n_el_bins: int, per_bin: int) -> pd.DataFrame:
    """Pick visits spread evenly over an Az x El grid.

    Reads azimuth/elevation for every readable night, bins the (az, el) plane,
    and keeps up to ``per_bin`` visits per occupied cell so the sample spans
    the full range rather than clustering where most images were taken.
    """
    frames = []
    for path in sorted(glob.glob(os.path.join(parquet_dir, "nightly_aos_table_*.parquet"))):
        if os.path.basename(path) in CORRUPTED_FILES:
            continue
        try:
            d = pd.read_parquet(path, columns=["visit_id", "azimuth", "elevation"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip unreadable %s: %s", os.path.basename(path), exc)
            continue
        d["night"] = path.split("_")[3]
        frames.append(d)
    df = pd.concat(frames, ignore_index=True).dropna(subset=["azimuth", "elevation"])
    df["visit_id"] = df["visit_id"].astype(int)

    az_edges = np.linspace(0, 360, n_az_bins + 1)
    el_edges = np.linspace(df.elevation.min(), df.elevation.max(), n_el_bins + 1)
    df["az_bin"] = np.clip(np.digitize(df.azimuth, az_edges) - 1, 0, n_az_bins - 1)
    df["el_bin"] = np.clip(np.digitize(df.elevation, el_edges) - 1, 0, n_el_bins - 1)

    # Deterministic pick: take the first `per_bin` visits (sorted) per cell.
    picked = (
        df.sort_values("visit_id")
        .groupby(["az_bin", "el_bin"], group_keys=False)
        .head(per_bin)
        .reset_index(drop=True)
    )
    logger.info(
        "Selected %d visits across %d occupied Az/El cells (grid %dx%d, <=%d/cell)",
        len(picked), picked.groupby(["az_bin", "el_bin"]).ngroups,
        n_az_bins, n_el_bins, per_bin,
    )
    return picked


def scan(picked: pd.DataFrame, out_csv: str) -> None:
    """Run the rotRate comparison for each selected visit, writing a CSV."""
    client = makeEfdClient()
    butler = makeDefaultButler("LSSTCam", embargo=False)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    done: set[int] = set()
    if os.path.exists(out_csv):
        try:
            done = {int(v) for v in pd.read_csv(out_csv, usecols=["visit_id"])["visit_id"]}
        except Exception:  # noqa: BLE001
            done = set()
    new_file = not os.path.exists(out_csv)
    fh = open(out_csv, "a", newline="")
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()

    n = len(picked)
    t0 = time.time()
    for i, rec in enumerate(picked.itertuples(index=False)):
        vid = int(rec.visit_id)
        if vid in done:
            continue
        row = {k: "" for k in FIELDS}
        row.update(visit_id=vid, night=rec.night)
        try:
            er = getExpRecordFromDataId(butler, {"exposure": vid, "instrument": "LSSTCam"})
            me, md = calculateMountErrors(er, client)
            if me is None:
                row["status"] = "skipped_nontracking"
            else:
                rd = md.rotationData
                rv = np.asarray(rd["actualPosition"], dtype=float)
                rm = np.asarray(rd["linearModel"], dtype=float)
                # dT cancels in the ratio; use 1.0 for the span.
                actual = rv[-1] - rv[0]
                model = rm[-1] - rm[0]
                pct = (actual - model) / actual * 100.0 if actual != 0 else np.nan
                row.update(
                    azimuth=f"{er.azimuth:.4f}",
                    elevation=f"{90.0 - er.zenith_angle:.4f}",
                    zenith_angle=f"{er.zenith_angle:.4f}",
                    actualRate=f"{actual:.8e}",
                    modelRate=f"{model:.8e}",
                    percentError=f"{pct:.4f}",
                    rotRms=f"{me.rotRms:.4f}",
                    imageImpactRms=f"{me.imageImpactRms:.4f}",
                    status="ok",
                )
        except Exception as exc:  # noqa: BLE001 - one bad image must not kill the scan
            row["status"] = f"error: {type(exc).__name__}"
            logger.warning("visit %s errored: %s: %s", vid, type(exc).__name__, exc)
        writer.writerow(row)
        if (i + 1) % 20 == 0:
            fh.flush()
            logger.info("  %d/%d (%.1f s)", i + 1, n, time.time() - t0)
    fh.flush()
    fh.close()
    logger.info("Scan done: %d visits in %.1f s -> %s", n, time.time() - t0, out_csv)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parquet-dir", default=DEFAULT_PARQUET_DIR)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--az-bins", type=int, default=12)
    p.add_argument("--el-bins", type=int, default=8)
    p.add_argument("--per-bin", type=int, default=3)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    logging.getLogger("lsst.summit.utils.simonyi.mountAnalysis").setLevel(logging.ERROR)
    picked = select_visits(args.parquet_dir, args.az_bins, args.el_bins, args.per_bin)
    scan(picked, args.out)
    return 0


from lsst.summit.utils.efdUtils import makeEfdClient  # noqa: E402
from lsst.summit.utils.simonyi.mountAnalysis import calculateMountErrors  # noqa: E402
from lsst.summit.utils.butlerUtils import (  # noqa: E402
    getExpRecordFromDataId,
    makeDefaultButler,
)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
