"""Scan the azimuth/elevation timestamp-delta *distribution* across visits.

Follow-up to timestamp_dropout_scan.py: instead of a single ">75 ms" fraction,
record how the successive-timestamp deltas break down into bands (~50 / 100 /
150 / 200+ ms) plus the maximum gap, for each axis. This tests whether the
long gaps (150 ms+) -- rather than merely-irregular 100 ms gaps -- are what
drove the pre-fix false positives.

Nominal telemetry cadence is 50 ms, so bands are centred on multiples of that:
  b50:   <  75 ms   (nominal single step)
  b100:  75-125 ms  (one sample skipped)
  b150: 125-175 ms  (two skipped)
  b200: >=175 ms    (three or more skipped / long stall)
Counts are reported as percentages of the successive-delta count, per axis,
along with the maximum delta seen (ms).
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
    "/sdf/home/c/cslage/claude/test_summit_utils/results_timestamp/timestamp_breakdown.csv"
)
CORRUPTED_FILES = {"nightly_aos_table_20260619_summit.parquet"}

# Band edges in seconds (nominal cadence 50 ms).
EDGES = [0.075, 0.125, 0.175]  # => <75, 75-125, 125-175, >=175

logger = logging.getLogger("timestamp_breakdown_scan")

FIELDS = [
    "visit_id", "night", "azimuth", "elevation",
    "az_b100", "az_b150", "az_b200", "az_maxms",
    "el_b100", "el_b150", "el_b200", "el_maxms",
    "status",
]


def band_pcts(diffs: np.ndarray) -> tuple[float, float, float, float]:
    """Return (% 75-125 ms, % 125-175 ms, % >=175 ms, max ms) for deltas."""
    d = diffs[~np.isnan(diffs)]
    if len(d) == 0:
        return 0.0, 0.0, 0.0, 0.0
    n = len(d)
    b100 = np.sum((d > EDGES[0]) & (d <= EDGES[1])) / n * 100.0
    b150 = np.sum((d > EDGES[1]) & (d <= EDGES[2])) / n * 100.0
    b200 = np.sum(d > EDGES[2]) / n * 100.0
    return b100, b150, b200, float(d.max() * 1000.0)


def timestamp_breakdown(mountData) -> dict[str, float]:
    """Band breakdown of az and el successive-timestamp deltas."""
    az = mountData.azimuthData.actualPositionTimestamp.diff().values
    el = mountData.elevationData.actualPositionTimestamp.diff().values
    a100, a150, a200, amax = band_pcts(az)
    e100, e150, e200, emax = band_pcts(el)
    return dict(
        az_b100=a100, az_b150=a150, az_b200=a200, az_maxms=amax,
        el_b100=e100, el_b150=e150, el_b200=e200, el_maxms=emax,
    )


def select_visits(parquet_dir: str, per_night: int) -> pd.DataFrame:
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
        d = d.sort_values("visit_id").reset_index(drop=True)
        if per_night > 0 and len(d) > per_night:
            idx = np.linspace(0, len(d) - 1, per_night).round().astype(int)
            d = d.iloc[np.unique(idx)].reset_index(drop=True)
        frames.append(d)
    picked = pd.concat(frames, ignore_index=True)
    logger.info("Selected %d visits (<=%d/night)", len(picked), per_night)
    return picked


def scan(picked: pd.DataFrame, out_csv: str) -> None:
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
        night = vid // 100000
        row = {k: "" for k in FIELDS}
        row.update(visit_id=vid, night=night,
                   azimuth=f"{rec.azimuth:.4f}", elevation=f"{rec.elevation:.4f}")
        try:
            er = getExpRecordFromDataId(butler, {"exposure": vid, "instrument": "LSSTCam"})
            mountData = getAzElRotHexDataForExposure(client, er)
            b = timestamp_breakdown(mountData)
            for k, v in b.items():
                row[k] = f"{v:.3f}"
            row["status"] = "ok"
        except Exception as exc:  # noqa: BLE001
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
    p.add_argument("--per-night", type=int, default=0, help="max visits per night; 0 = all")
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


from lsst.summit.utils.efdUtils import makeEfdClient  # noqa: E402
from lsst.summit.utils.simonyi.mountData import getAzElRotHexDataForExposure  # noqa: E402
from lsst.summit.utils.butlerUtils import (  # noqa: E402
    getExpRecordFromDataId,
    makeDefaultButler,
)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
