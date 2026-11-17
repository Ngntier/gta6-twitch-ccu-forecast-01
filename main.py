"""Forecast GTA VI's launch-day Twitch viewers hour by hour.

Entry for make.fun's gta6-twitch-ccu-forecast-01 (program/v1, Python 3.13,
standard library only, no network). For each case file it reads the Twitch
viewer count of a game's launch day up to the end of its first hour and
forecasts hours 2..24 as five quantiles each (levels 0.05, 0.25, 0.5, 0.75,
0.95), scored by pinball loss on the log scale.

How it forecasts
----------------
The case ships the hourly curves of dozens of past big launches
(`comparables`). For each comparable we take its release-day curve as
log-ratios to its own hour-1 print, ln(viewers_h / viewers_1), and build
the forecast from the distribution of those ratios:

1. Pool: only launches whose unlock rolled at midnight local time, the way
   GTA VI unlocks (and the way every practice launch did). Launches with a
   single global unlock hour have a different day shape -- their hour 1 can
   sit hours before the unlock -- and would smear the curve. If too few
   rolling launches are available, fall back to every comparable.
2. Alignment: a rolling launch unlocks at US Eastern midnight, which is
   04:00 UTC in daylight time and 05:00 UTC in standard time. The US unlock
   wave is a large part of the day's shape, so each comparable's curve is
   shifted by the difference between the target's and its own US-East
   unlock hour (inferred from the release date via US daylight-saving
   rules), so the waves line up instead of blurring across two hours.
3. Similarity weighting: comparables are weighted by how close their
   hour-1 print is to the target's, exp(-|ln v1 - ln p1| / 0.75) in log
   space, keeping the 8 nearest. Launch size is the strongest predictor
   of the day's shape we found: huge launches hold their audience
   differently from small ones.
4. Quantiles: per hour, the weighted empirical quantiles of the aligned
   log-ratios, exponentiated and scaled by the target's hour-1 print.

Local results (practice cases p00..p03, scored against SullyGnome's
historical hourly averages): mean pinball loss 0.200, typical miss 36.7%,
90% coverage 0.870 -- against the house persistence baseline's 0.387 loss,
82.8% miss and 0.783 coverage. Leave-one-out over the 12 rolling
comparables gives a loss of 0.243 (persistence baseline: 0.357 there).

Deterministic: no randomness, no wall-clock or network input; two runs on
the same case write byte-identical files.
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]

# Log-space bandwidth for the hour-1 similarity weight, and how many of the
# nearest comparables to keep. Tuned on leave-one-out over the rolling pool.
BANDWIDTH = 0.75
TOP_K = 8

# Minimum rolling launches needed to use the rolling-only pool.
MIN_ROLLING = 4

# Fallback spread (log units per sqrt hour) if no comparable is usable.
FALLBACK_SIGMA = 0.15


def normal_quantile(p):
    """The standard normal's quantile at p (Acklam's rational approximation).

    Used only by the no-comparables fallback, so the spread matches whatever
    levels the case asks for instead of assuming the standard five.
    """
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285984469534e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        return num / den
    if p <= p_high:
        q = p - 0.5
        r = q * q
        num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        return num / den
    q = math.sqrt(-2 * math.log(1 - p))
    num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
    den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    return -num / den


def parse_ts(text):
    """An ISO-8601 UTC timestamp into an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _nth_sunday(year, month, n):
    first = datetime(year, month, 1, tzinfo=None)
    days_to_sunday = (6 - first.weekday()) % 7
    return first + timedelta(days=days_to_sunday + 7 * (n - 1))


def east_unlock_hour(day_start):
    """US Eastern midnight as a UTC hour for a launch on `day_start`.

    Rolling-midnight launches unlock at Eastern midnight: 04:00 UTC while
    the US is on daylight time (2nd Sunday of March to 1st Sunday of
    November) and 05:00 UTC otherwise.
    """
    day = parse_ts(day_start).date()
    dst_start = _nth_sunday(day.year, 3, 2).date()
    dst_end = _nth_sunday(day.year, 11, 1).date()
    return 4 if dst_start <= day < dst_end else 5


def is_rolling(text):
    """True when a free-text unlock note describes a rolling midnight launch."""
    lowered = (text or "").lower()
    return "midnight local" in lowered or "rolling" in lowered


def release_day_series(comp):
    """A comparable's release-day hourly viewers as {hour: viewers}.

    Hours 1..24 from day_start (00:00 UTC). Returns None when any hour is
    missing, so a sparse curve never enters the pool half-built.
    """
    try:
        day_start = parse_ts(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    by_hour = {}
    for row in comp.get("hourly") or []:
        try:
            at = parse_ts(row["from"])
            viewers = row["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None:
            continue
        by_hour[at] = viewers
    series = {}
    for hour in range(1, 25):
        viewers = by_hour.get(day_start + timedelta(hours=hour - 1))
        if viewers is None:
            return None
        series[hour] = viewers
    return series


def shift_ratios(ratios, delta):
    """Shift a log-ratio curve by `delta` hours (target minus comparable).

    Hour 1's ratio is 0 by definition; lookups clamp to hours 1..24.
    """
    full = {1: 0.0}
    full.update(ratios)
    return {h: full[min(24, max(1, h - delta))] for h in range(2, 25)}


def weighted_quantile(pairs, q):
    """Weighted quantile over (value, weight) pairs, interpolated on the
    cumulative weight. `pairs` may arrive in any order."""
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    if total <= 0:
        return ordered[len(ordered) // 2][0]
    target = q * total
    acc = 0.0
    for i, (value, weight) in enumerate(ordered):
        before = acc
        acc += weight
        if acc >= target:
            if i == 0 or weight <= 0:
                return value
            prev = ordered[i - 1][0]
            return prev + (target - before) / weight * (value - prev)
    return ordered[-1][0]


def build_pool(case):
    """The comparables usable as ratio curves, with their metadata."""
    pool = []
    for comp in case.get("comparables") or []:
        series = release_day_series(comp)
        if series is None:
            continue
        first = series[1]
        if first is None or first < 1:
            continue
        ratios = {
            h: math.log(max(1, series[h]) / max(1, first)) for h in range(2, 25)
        }
        pool.append(
            {
                "v1": first,
                "ratios": ratios,
                "rolling": is_rolling(comp.get("unlock")),
                "day_start": comp["day_start"],
            }
        )
    return pool


def round2(value):
    """Round to 2 decimals, half up; whole numbers come back as ints."""
    scaled = value * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    rounded = whole / 100
    return int(rounded) if rounded.is_integer() else rounded


def clean_quantiles(raw):
    """Clamp to the contest's bounds and enforce a non-decreasing row."""
    cleaned = []
    floor = 0.0
    for value in raw:
        value = min(1e9, max(1.0, value))
        value = round2(value)
        value = max(value, floor)
        cleaned.append(value)
        floor = value
    return cleaned


def forecast_case(case, name):
    """Forecast one case: {"hours": [{"hour": h, "q": [5 numbers]}, ...]}."""
    levels = case.get("levels") or LEVELS
    if (
        len(levels) != len(LEVELS)
        or any(not (0.0 < lv < 1.0) for lv in levels)
        or levels != sorted(levels)
    ):
        levels = LEVELS

    known = case.get("known") or []
    first = next((k for k in known if k.get("hour") == 1), None)
    if first is not None and first.get("viewers"):
        p1 = max(1.0, float(first["viewers"]))
    else:
        # No hour-1 print: fall back to the largest pre-checkpoint point.
        history = case.get("history") or []
        p1 = max([1.0] + [float(p.get("viewers") or 0) for p in history])

    hours = []
    for row in case.get("forecast") or []:
        hour = row.get("hour") if isinstance(row, dict) else None
        if isinstance(hour, int) and not isinstance(hour, bool):
            hours.append(hour)
    if sorted(hours) != list(range(2, 25)):
        hours = list(range(2, 25))

    pool = build_pool(case)
    target_rolling = is_rolling(case.get("notes")) if case.get("notes") else True
    if target_rolling:
        rolling = [entry for entry in pool if entry["rolling"]]
        if len(rolling) >= MIN_ROLLING:
            pool = rolling
    target_east = None
    if target_rolling and case.get("day_start"):
        try:
            target_east = east_unlock_hour(case["day_start"])
        except (TypeError, ValueError):
            target_east = None

    rows = []
    if not pool:
        # No comparable to learn from: persistence with a widening spread.
        z_values = [normal_quantile(lv) for lv in levels]
        for h in hours:
            spread = FALLBACK_SIGMA * math.sqrt(h - 1)
            rows.append(
                {
                    "hour": h,
                    "q": clean_quantiles(
                        [p1 * math.exp(z * spread) for z in z_values]
                    ),
                }
            )
        return {"hours": rows}

    weighted = []
    for entry in pool:
        weight = math.exp(-abs(math.log(entry["v1"]) - math.log(p1)) / BANDWIDTH)
        ratios = entry["ratios"]
        if target_east is not None:
            try:
                delta = target_east - east_unlock_hour(entry["day_start"])
            except (TypeError, ValueError):
                delta = 0
            ratios = shift_ratios(ratios, delta)
        weighted.append((weight, ratios))
    weighted.sort(key=lambda item: -item[0])
    weighted = weighted[:TOP_K]

    for h in hours:
        pairs = [(ratios[h], weight) for weight, ratios in weighted]
        log_q = [weighted_quantile(pairs, lv) for lv in levels]
        rows.append(
            {"hour": h, "q": clean_quantiles([math.exp(v) * p1 for v in log_q])}
        )
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / f"{path.stem}.out").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
