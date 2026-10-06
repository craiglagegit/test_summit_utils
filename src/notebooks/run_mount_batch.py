"""Batch-run the DM-56137 mount-motion analysis over many nights of images.

Reads visit ids from the nightly parquet tables, runs
``calculateMountErrors`` / ``plotMountErrors`` from the ``tickets/DM-56137``
branch of ``summit_utils`` on each image, saves a mount plot for every image,
and records the "Mount motion image degradation" metric
(``MountErrors.imageImpactRms``) so flagged images can be reviewed for false
positives.

Classification thresholds match the branch's cell-colour constants
(``MOUNT_IMAGE_WARNING_LEVEL`` / ``MOUNT_IMAGE_BAD_LEVEL`` in
``mountAnalysis.py``): yellow when ``imageImpactRms > 0.05`` and red when
``imageImpactRms > 0.10``.

The run is resumable: visits already present in a night's results CSV (with an
existing plot) are skipped, and results are flushed to disk per night.
"""

from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import sys
import time

import pandas as pd

# Thresholds (kept in sync with mountAnalysis.MOUNT_IMAGE_WARNING_LEVEL /
# MOUNT_IMAGE_BAD_LEVEL; hard-coded here so the classification is explicit).
YELLOW_LEVEL = 0.05
RED_LEVEL = 0.10

DEFAULT_PARQUET_DIR = (
    "/sdf/home/c/cslage/claude/claude-scons_db/src/claude_scons_db/parquet_files"
)
DEFAULT_PLOTS_DIR = "/sdf/home/c/cslage/claude/test_summit_utils/plots"
DEFAULT_RESULTS_DIR = "/sdf/home/c/cslage/claude/test_summit_utils/results"

# Parquet files that cannot be read; skipped with a warning.
CORRUPTED_FILES = {"nightly_aos_table_20260619_summit.parquet"}

RESULT_FIELDS = [
    "visit_id",
    "imageImpactRms",
    "azRms",
    "elRms",
    "rotRms",
    "classification",
    "status",
]

logger = logging.getLogger("run_mount_batch")


def classify(image_impact_rms: float) -> str:
    """Return the RubinTV-style colour for a degradation value."""
    if image_impact_rms > RED_LEVEL:
        return "red"
    if image_impact_rms > YELLOW_LEVEL:
        return "yellow"
    return "green"


def parquet_path_for_night(parquet_dir: str, night: str) -> str | None:
    """Return the parquet file path for a dayObs, or None if not found."""
    matches = glob.glob(os.path.join(parquet_dir, f"nightly_aos_table_{night}_*.parquet"))
    return matches[0] if matches else None


def visit_ids_for_night(parquet_path: str) -> list[int]:
    """Read just the visit_id column, cast float ids to sorted unique ints."""
    df = pd.read_parquet(parquet_path, columns=["visit_id"])
    vids = sorted({int(v) for v in df["visit_id"].dropna().tolist()})
    return vids


def discover_all_nights(parquet_dir: str) -> list[str]:
    """Return sorted dayObs strings for every readable nightly parquet file."""
    nights = []
    for path in sorted(glob.glob(os.path.join(parquet_dir, "nightly_aos_table_*.parquet"))):
        name = os.path.basename(path)
        if name in CORRUPTED_FILES:
            logger.warning("Skipping known-corrupted parquet file: %s", name)
            continue
        # nightly_aos_table_<dayObs>_summit.parquet
        night = name.split("_")[3]
        nights.append(night)
    return nights


def load_done_visits(results_csv: str) -> set[int]:
    """Return visit ids already recorded in a night's results CSV."""
    if not os.path.exists(results_csv):
        return set()
    try:
        df = pd.read_csv(results_csv, usecols=["visit_id"])
        return {int(v) for v in df["visit_id"].tolist()}
    except Exception:
        return set()


def process_night(
    night: str,
    parquet_dir: str,
    plots_dir: str,
    results_dir: str,
    butler,
    client,
    figure,
    calculateMountErrors,
    plotMountErrors,
    plot_flagged_only: bool = False,
) -> dict[str, int]:
    """Process every image for one night; return per-status counts.

    When ``plot_flagged_only`` is True, only yellow/red images get a saved
    plot; every image is still processed and recorded in the results CSV.
    """
    parquet_path = parquet_path_for_night(parquet_dir, night)
    counts = {
        "processed": 0,
        "green": 0,
        "yellow": 0,
        "red": 0,
        "skipped_nontracking": 0,
        "errored": 0,
        "already_done": 0,
    }
    if parquet_path is None:
        logger.error("No parquet file found for night %s; skipping.", night)
        return counts
    if os.path.basename(parquet_path) in CORRUPTED_FILES:
        logger.error("Parquet for night %s is corrupted; skipping.", night)
        return counts

    vids = visit_ids_for_night(parquet_path)
    results_csv = os.path.join(results_dir, f"night_{night}_results.csv")
    done = load_done_visits(results_csv)

    new_file = not os.path.exists(results_csv)
    fh = open(results_csv, "a", newline="")
    writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
    if new_file:
        writer.writeheader()

    flagged: list[dict] = []  # yellow/red rows for the aggregate lists
    n = len(vids)
    logger.info("Night %s: %d visits (%d already done)", night, n, len(done & set(vids)))

    t_night = time.time()
    for i, vid in enumerate(vids):
        plot_path = os.path.join(plots_dir, f"Mount_Plot_Hex_{vid}.png")
        # A visit is "done" if it is already in the results CSV. In
        # all-plots mode we also require the plot to exist (so a crash
        # mid-plot re-runs it); in flagged-only mode green images have no
        # plot, so the CSV row alone is authoritative.
        if vid in done and (plot_flagged_only or os.path.exists(plot_path)):
            counts["already_done"] += 1
            continue

        row = {
            "visit_id": vid,
            "imageImpactRms": "",
            "azRms": "",
            "elRms": "",
            "rotRms": "",
            "classification": "",
            "status": "",
        }
        try:
            dataId = {"exposure": vid, "instrument": "LSSTCam"}
            expRecord = getExpRecordFromDataId(butler, dataId)
            mountErrors, mountData = calculateMountErrors(expRecord, client)
            if mountErrors is None:
                row["status"] = "skipped_nontracking"
                counts["skipped_nontracking"] += 1
            else:
                iir = mountErrors.imageImpactRms
                cls = classify(iir)
                row.update(
                    imageImpactRms=f"{iir:.6f}",
                    azRms=f"{mountErrors.azRms:.6f}",
                    elRms=f"{mountErrors.elRms:.6f}",
                    rotRms=f"{mountErrors.rotRms:.6f}",
                    classification=cls,
                    status="ok",
                )
                if not plot_flagged_only or cls in ("yellow", "red"):
                    plotMountErrors(
                        mountData, mountErrors, figure=figure, saveFilename=plot_path
                    )
                counts["processed"] += 1
                counts[cls] += 1
                if cls in ("yellow", "red"):
                    flagged.append(
                        {
                            "visit_id": vid,
                            "night": night,
                            "imageImpactRms": f"{iir:.6f}",
                            "classification": cls,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - one bad image must not kill the run
            row["status"] = f"error: {type(exc).__name__}"
            counts["errored"] += 1
            logger.warning("visit %s errored: %s: %s", vid, type(exc).__name__, exc)

        writer.writerow(row)
        if (i + 1) % 50 == 0:
            fh.flush()
            elapsed = time.time() - t_night
            logger.info(
                "  night %s: %d/%d (%.1f s, y=%d r=%d skip=%d err=%d)",
                night, i + 1, n, elapsed,
                counts["yellow"], counts["red"],
                counts["skipped_nontracking"], counts["errored"],
            )

    fh.flush()
    fh.close()
    append_flagged(results_dir, flagged)
    logger.info(
        "Night %s done in %.1f s: processed=%d green=%d yellow=%d red=%d skip=%d err=%d",
        night, time.time() - t_night, counts["processed"], counts["green"],
        counts["yellow"], counts["red"], counts["skipped_nontracking"], counts["errored"],
    )
    return counts


def append_flagged(results_dir: str, flagged: list[dict]) -> None:
    """Append yellow/red rows to the aggregate yellow_list / red_list CSVs."""
    fields = ["visit_id", "night", "imageImpactRms", "classification"]
    for colour, fname in (("yellow", "yellow_list.csv"), ("red", "red_list.csv")):
        rows = [r for r in flagged if r["classification"] == colour]
        if not rows:
            continue
        path = os.path.join(results_dir, fname)
        new_file = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)


def write_summary(results_dir: str, per_night: dict[str, dict[str, int]]) -> None:
    """Write a human-readable per-night summary of the run."""
    path = os.path.join(results_dir, "run_summary.txt")
    totals: dict[str, int] = {}
    with open(path, "w") as fh:
        fh.write("Mount-motion batch run summary (metric = imageImpactRms)\n")
        fh.write(f"Thresholds: yellow > {YELLOW_LEVEL}, red > {RED_LEVEL}\n\n")
        header = "night        processed  green  yellow   red  skip   err  done_before\n"
        fh.write(header)
        for night in sorted(per_night):
            c = per_night[night]
            fh.write(
                f"{night}   {c['processed']:9d}  {c['green']:5d}  {c['yellow']:6d}  "
                f"{c['red']:4d}  {c['skipped_nontracking']:4d}  {c['errored']:4d}  "
                f"{c['already_done']:11d}\n"
            )
            for k, v in c.items():
                totals[k] = totals.get(k, 0) + v
        fh.write("\nTOTALS: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())) + "\n")
    logger.info("Wrote summary to %s", path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--nights", nargs="+", help="dayObs values, e.g. 20260603 20260525")
    grp.add_argument("--all", action="store_true", help="process every readable night")
    p.add_argument("--parquet-dir", default=DEFAULT_PARQUET_DIR)
    p.add_argument("--plots-dir", default=DEFAULT_PLOTS_DIR)
    p.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR)
    p.add_argument(
        "--plot-flagged-only",
        action="store_true",
        help="only save plots for yellow/red images (all images still processed)",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # The per-image "calculating errors vs astropy" warnings from mountAnalysis
    # are expected and very noisy over thousands of images; quiet them.
    logging.getLogger("lsst.summit.utils.simonyi.mountAnalysis").setLevel(logging.ERROR)

    os.makedirs(args.plots_dir, exist_ok=True)
    os.makedirs(args.results_dir, exist_ok=True)

    if args.all:
        nights = discover_all_nights(args.parquet_dir)
    else:
        nights = list(args.nights)
    logger.info("Processing %d night(s): %s", len(nights), ", ".join(nights))
    logger.info(
        "Plot mode: %s", "flagged-only (yellow/red)" if args.plot_flagged_only else "all images"
    )

    # Heavy imports and client/butler init happen once.
    t0 = time.time()
    from lsst.utils.plotting.figures import make_figure

    client = makeEfdClient()
    butler = makeDefaultButler("LSSTCam", embargo=False)
    figure = make_figure(figsize=(12, 8))
    logger.info("Init done in %.1f s", time.time() - t0)

    per_night: dict[str, dict[str, int]] = {}
    for night in nights:
        per_night[night] = process_night(
            night, args.parquet_dir, args.plots_dir, args.results_dir,
            butler, client, figure, calculateMountErrors, plotMountErrors,
            plot_flagged_only=args.plot_flagged_only,
        )
        write_summary(args.results_dir, per_night)

    logger.info("All done.")
    return 0


# Imports that require the LSST stack to be set up; done at module import so
# argument parsing errors surface before the slow stack import in main().
from lsst.summit.utils.efdUtils import makeEfdClient  # noqa: E402
from lsst.summit.utils.simonyi.mountAnalysis import (  # noqa: E402
    calculateMountErrors,
    plotMountErrors,
)
from lsst.summit.utils.butlerUtils import (  # noqa: E402
    getExpRecordFromDataId,
    makeDefaultButler,
)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
