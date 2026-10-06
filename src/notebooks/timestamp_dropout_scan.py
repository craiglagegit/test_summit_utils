"""Scan azimuth/elevation telemetry timestamp dropouts across nights.

Motivation: the DM-56137 linear-model fix was needed because the azimuth
telemetry timestamps are sometimes not evenly spaced -- nominally 50 ms apart,
but occasionally 100 or 150 ms (dropped samples). This appears to affect
azimuth only (not elevation) and is suspected to be related to azimuth encoder
head damage that began at some point. This script quantifies, per image, the
fraction of successive timestamp deltas longer than 75 ms, so we can see:

  (1) whether every night shows some azimuth dropouts or whether it started on
      a particular date, and
  (2) whether the high-dropout images cluster at particular azimuths.

For each sampled visit it records night, azimuth, elevation, and the az/el
percentage of timestamp deltas exceeding the 75 ms limit (the user's
``timestampDiffs`` snippet).
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
DEFAULT_OUT = (
    "/sdf/home/c/cslage/claude/test_summit_utils/results_timestamp/timestamp_dropout.csv"
)
CORRUPTED_FILES = {"nightly_aos_table_20260619_summit.parquet"}
DIFF_LIMIT = 0.075  # 75 ms

logger = logging.getLogger("timestamp_dropout_scan")

FIELDS = ["visit_id", "night", "azimuth", "elevation", "azPctGT75ms", "elPctGT75ms", "status"]


def timestamp_diffs(mountData) -> tuple[float, float]:
    """Fraction (%) of successive az/el timestamp deltas longer than 75 ms.

    This is the user's ``timestampDiffs`` snippet verbatim in intent.
    """
    azData = mountData.azimuthData
    elData = mountData.elevationData
    azTimestampDiff = azData.actualPositionTimestamp.diff().values
    elTimestampDiff = elData.actualPositionTimestamp.diff().values
    azGT = azTimestampDiff[azTimestampDiff > DIFF_LIMIT]
    elGT = elTimestampDiff[elTimestampDiff > DIFF_LIMIT]
    azPct = len(azGT) / len(azTimestampDiff) * 100.0
    elPct = len(elGT) / len(elTimestampDiff) * 100.0
    return azPct, elPct


def select_visits(parquet_dir: str, per_night: int) -> pd.DataFrame:
    """Pick up to ``per_night`` visits per night, evenly spaced across the night.

    Even spacing (rather than the first N) ensures the sample spans the whole
    night's azimuth range, not just the first pointing.
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
        d = d.dropna(subset=["azimuth", "elevation"]).copy()
        d["visit_id"] = d["visit_id"].astype(int)
        d["night"] = path.split("_")[3]
        d = d.sort_values("visit_id").reset_index(drop=True)
        if per_night > 0 and len(d) > per_night:
            idx = np.linspace(0, len(d) - 1, per_night).round().astype(int)
            d = d.iloc[np.unique(idx)].reset_index(drop=True)
        frames.append(d)
    picked = pd.concat(frames, ignore_index=True)
    logger.info(
        "Selected %d visits across %d nights (<=%d/night)",
        len(picked), picked.night.nunique(), per_night,
    )
    return picked


def scan(picked: pd.DataFrame, out_csv: str) -> None:
    """Compute az/el timestamp-dropout fractions for each selected visit."""
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
        row.update(visit_id=vid, night=rec.night,
                   azimuth=f"{rec.azimuth:.4f}", elevation=f"{rec.elevation:.4f}")
        try:
            er = getExpRecordFromDataId(butler, {"exposure": vid, "instrument": "LSSTCam"})
            mountData = getAzElRotHexDataForExposure(client, er)
            azPct, elPct = timestamp_diffs(mountData)
            row.update(azPctGT75ms=f"{azPct:.3f}", elPctGT75ms=f"{elPct:.3f}", status="ok")
        except Exception as exc:  # noqa: BLE001 - one bad image must not kill the scan
            row["status"] = f"error: {type(exc).__name__}"
            logger.warning("visit %s errored: %s: %s", vid, type(exc).__name__, exc)
        writer.writerow(row)
        if (i + 1) % 50 == 0:
            fh.flush()
            logger.info("  %d/%d (%.1f s)", i + 1, n, time.time() - t0)
    fh.flush()
    fh.close()
    logger.info("Scan done: %d visits in %.1f s -> %s", n, time.time() - t0, out_csv)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parquet-dir", default=DEFAULT_PARQUET_DIR)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--per-night", type=int, default=120,
                   help="max visits per night (evenly spaced); 0 = all")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    logging.getLogger("lsst.summit.utils.simonyi.mountAnalysis").setLevel(logging.ERROR)
    picked = select_visits(args.parquet_dir, args.per_night)
    scan(picked, args.out)
    return 0


# Note: we use getAzElRotHexDataForExposure directly (not calculateMountErrors)
# because we only need the raw telemetry timestamps, not the full error calc --
# this is faster and avoids the pointing-model work entirely.
from lsst.summit.utils.efdUtils import makeEfdClient  # noqa: E402
from lsst.summit.utils.simonyi.mountData import getAzElRotHexDataForExposure  # noqa: E402
from lsst.summit.utils.butlerUtils import (  # noqa: E402
    getExpRecordFromDataId,
    makeDefaultButler,
)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
