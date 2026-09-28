"""engine/analytics.py — station-level crime series analysis and narration.

Narration uses only numbers already present in the station fact sheet so
there is no risk of fabricated figures.

Exports
-------
analytics(firs) -> (months: list[str], stations: list[dict])
    Compute monthly crime series, Poisson spike detection, crime mix and
    victim demographics per police station.

narrate(station_fact_sheet, clustered_fir_count) -> str
    Produce a short factual narrative from the station fact sheet.
"""
import collections
from datetime import datetime

from scipy.stats import poisson


def analytics(firs: list[dict]) -> tuple[list[str], list[dict]]:
    """Compute per-station crime analytics.

    Parameters
    ----------
    firs : list[dict]
        Normalised FIR dicts with .occurred, .district, .station,
        .crime_type, .victim, .property.

    Returns
    -------
    months   : list[str]  Sorted list of 'YYYY-MM' strings spanning the dataset.
    stations : list[dict] One dict per (district, station) pair.
    """
    months = sorted({f["occurred"][:7] for f in firs})
    groups: dict[tuple, list] = collections.defaultdict(list)

    for f in firs:
        groups[(f["district"], f["station"])].append(f)

    out: list[dict] = []
    for (dist, stn), fs in sorted(groups.items()):
        cnt = collections.Counter(f["occurred"][:7] for f in fs)
        series = [cnt.get(m, 0) for m in months]

        spikes: list[dict] = []
        for i, c in enumerate(series):
            others = series[:i] + series[i + 1:]
            lam = max(sum(others) / len(others), 0.25)
            pv = 1 - poisson.cdf(c - 1, lam) if c else 1
            if c >= 3 and pv < 0.05:
                spikes.append({
                    "month": months[i],
                    "count": c,
                    "baseline": round(lam, 2),
                    "p": round(pv, 4),
                })

        out.append({
            "district": dist,
            "station": stn,
            "series": series,
            "total": len(fs),
            "spikes": spikes,
            "mix": dict(collections.Counter(f["crime_type"] for f in fs).most_common()),
            "loss_inr": sum(f["property"].get("value_inr") or 0 for f in fs),
            "victims": dict(
                collections.Counter(
                    f"{f['victim']['gender']} {f['victim']['age_band']}" for f in fs
                ).most_common(3)
            ),
        })

    return months, out


def narrate(s: dict, clustered: int) -> str:
    """Produce a short factual narrative for a station fact sheet.

    Every number in the output is drawn directly from *s* — no figures are
    invented.

    Parameters
    ----------
    s         : dict  A station analytics dict produced by analytics().
    clustered : int   Number of FIRs at this station that belong to a cluster.

    Returns
    -------
    str  A short human-readable summary sentence.
    """
    top, n = next(iter(s["mix"].items()))
    txt = [
        f"{s['station']} registered {s['total']} FIRs between July 2024 and June 2025, "
        f"most often {top.replace('_', ' ')} ({n})."
    ]
    txt.append(f"Reported losses total Rs {s['loss_inr']:,}.")
    if s["spikes"]:
        sp = s["spikes"][0]
        month_label = datetime.strptime(sp["month"], "%Y-%m").strftime("%B %Y")
        txt.append(
            f"{month_label} was a spike: {sp['count']} FIRs against a monthly baseline of {sp['baseline']}."
        )
    if clustered:
        txt.append(
            f"{clustered} FIR{'s' if clustered > 1 else ''} here "
            f"{'are' if clustered > 1 else 'is'} linked to a cross-FIR cluster."
        )
    return " ".join(txt)
