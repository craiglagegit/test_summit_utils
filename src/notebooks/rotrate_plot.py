"""Plot rotRate model error vs Az / El from rotrate_scan.csv.

Produces a 4-panel figure:
  (1) percentError vs azimuth, coloured by elevation
  (2) percentError vs elevation, coloured by azimuth
  (3) Az/El scatter coloured by signed percentError
  (4) Az/El scatter coloured by rotRms (what actually drives false positives)

Because the rotator rate passes through zero near az = 90/270 deg
(cos(az) -> 0), percentError there is numerically huge but physically
irrelevant; those points are drawn faintly and excluded from the colour
scale so the real elevation-dependent trend is visible.
"""

from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

CSV = "/sdf/home/c/cslage/claude/test_summit_utils/results_rotrate/rotrate_scan.csv"
OUT = "/sdf/home/c/cslage/claude/test_summit_utils/results_rotrate/rotrate_error_vs_azel.png"


def main() -> int:
    d = pd.read_csv(CSV)
    d = d[d.status == "ok"].copy()
    # Flag points near az 90/270 where cos(az)~0 => rate ~ 0 => % error blows up.
    d["near_EW"] = np.abs(np.cos(np.radians(d.azimuth))) < 0.15
    good = d[~d.near_EW]

    # Robust colour limit for percentError (ignore the near-EW blow-ups).
    pe_lim = np.nanpercentile(good.percentError.abs(), 98)
    pe_lim = max(pe_lim, 1.5)

    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    fig.suptitle(
        "Rotator rate model error vs Az/El  (DM-56137)\n"
        "percentError = (actualRate - modelRate) / actualRate * 100",
        fontsize=13,
    )

    # (1) vs azimuth
    ax = axes[0, 0]
    sc = ax.scatter(d.azimuth, d.percentError, c=d.elevation, cmap="viridis", s=22)
    ax.axhline(0, color="k", lw=0.5)
    ax.axhline(1, color="r", lw=0.5, ls="--")
    ax.axhline(-1, color="r", lw=0.5, ls="--")
    ax.set_ylim(-pe_lim * 2, pe_lim * 2)
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("rotRate percentError (%)")
    ax.set_title("vs Azimuth (±1% dashed; y-axis clipped)")
    for a in (90, 270):
        ax.axvline(a, color="gray", lw=0.5, ls=":")
    fig.colorbar(sc, ax=ax, label="Elevation (deg)")

    # (2) vs elevation
    ax = axes[0, 1]
    sc = ax.scatter(
        good.elevation, good.percentError.abs(), c=good.azimuth, cmap="twilight", s=22
    )
    ax.axhline(1, color="r", lw=0.5, ls="--")
    ax.set_xlabel("Elevation (deg)")
    ax.set_ylabel("|rotRate percentError| (%)")
    ax.set_title("vs Elevation (az~90/270 excluded; 1% dashed)")
    ax.set_ylim(0, pe_lim * 2)
    fig.colorbar(sc, ax=ax, label="Azimuth (deg)")

    # (3) Az/El map coloured by signed percentError
    ax = axes[1, 0]
    sc = ax.scatter(
        good.azimuth, good.elevation, c=good.percentError,
        cmap="coolwarm", vmin=-pe_lim, vmax=pe_lim, s=40, edgecolor="k", linewidth=0.2,
    )
    ax.scatter(
        d[d.near_EW].azimuth, d[d.near_EW].elevation,
        facecolors="none", edgecolors="gray", s=40, label="az~90/270 (rate~0)",
    )
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Elevation (deg)")
    ax.set_title("Az/El map, colour = signed percentError")
    ax.legend(loc="lower right", fontsize=8)
    fig.colorbar(sc, ax=ax, label="percentError (%)")

    # (4) Az/El map coloured by rotRms (the actual false-positive driver)
    ax = axes[1, 1]
    rr = d.rotRms.clip(upper=np.nanpercentile(d.rotRms, 97))
    sc = ax.scatter(d.azimuth, d.elevation, c=rr, cmap="inferno", s=40,
                    edgecolor="k", linewidth=0.2)
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Elevation (deg)")
    ax.set_title("Az/El map, colour = rotRms (arcsec)")
    fig.colorbar(sc, ax=ax, label="rotRms (arcsec, clipped)")

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT, dpi=120)
    print(f"wrote {OUT}")

    # Also a focused figure: |percentError| vs elevation, az~90/270 excluded,
    # with a running median to make the near-zenith rise obvious.
    fig2, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(good.elevation, good.percentError.abs(), s=20, alpha=0.6)
    bins = np.linspace(good.elevation.min(), good.elevation.max(), 10)
    centers = 0.5 * (bins[:-1] + bins[1:])
    med = [
        good.percentError.abs()[(good.elevation >= bins[i]) & (good.elevation < bins[i + 1])].median()
        for i in range(len(bins) - 1)
    ]
    ax.plot(centers, med, "r-o", lw=2, label="binned median")
    ax.axhline(1, color="k", ls="--", lw=0.8, label="1% level")
    ax.set_xlabel("Elevation (deg)")
    ax.set_ylabel("|rotRate percentError| (%)")
    ax.set_title("rotRate error rises toward zenith (high elevation)")
    ax.set_ylim(0, np.nanpercentile(good.percentError.abs(), 97) * 1.3)
    ax.legend()
    out2 = OUT.replace(".png", "_vs_el_focus.png")
    fig2.tight_layout()
    fig2.savefig(out2, dpi=120)
    print(f"wrote {out2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
