# GTA VI launch-day Twitch forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Ngntier/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Ngntier/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entry for make.fun's **gta6-twitch-ccu-forecast-01**: forecast, hour by hour,
how many people watch Grand Theft Auto VI live on Twitch over the 24 UTC hours
of its release day (2026-11-19), from everything recorded up to the end of its
first hour, as five quantiles an hour.

## The program

`main.py` (Python 3.13, standard library only, no network) reads every `*.in`
case in `$MAKEFUN_INPUT_DIR` (else `/input/`) and writes
`$MAKEFUN_OUTPUT_DIR/<id>.out` for each: `{"hours": [{"hour": 2, "q": [...]},
...]}` with 23 rows, hours 2..24, five quantiles each. Deterministic: two runs
on the same case write byte-identical files.

## Approach

The case ships the hourly Twitch curves of dozens of past big launches. The
forecaster treats each comparable as a release-day curve of log-ratios to its
own hour-1 print, `ln(viewers_h / viewers_1)`, and builds the forecast from
the distribution of those ratios:

1. **Rolling-midnight pool only.** GTA VI unlocks at midnight local time,
   rolling around the world. Launches with a single global unlock hour have a
   different day shape (their hour 1 can sit hours before the unlock), so they
   are left out of the pool. Falls back to all comparables if too few rolling
   launches are available.
2. **Unlock-time alignment.** A rolling launch unlocks at US Eastern midnight:
   04:00 UTC in daylight time, 05:00 UTC in standard time. Each comparable's
   curve is shifted by the difference between the target's and its own US-East
   unlock hour (inferred from the release date via US daylight-saving rules),
   so the US unlock waves line up instead of blurring across two hours.
3. **Similarity weighting.** Comparables are weighted by how close their
   hour-1 print is to the target's (`exp(-|ln v1 - ln p1| / 0.75)`), keeping
   the 8 nearest. Launch size was the strongest predictor of the day's shape
   in testing.
4. **Weighted empirical quantiles** per hour, exponentiated and scaled by the
   target's hour-1 print.

## Local results (practice cases)

Scored against SullyGnome's historical hourly averages for the four practice
launches (Marvel's Wolverine, Red Dead Redemption 2, God of War Ragnarok,
Marvel's Spider-Man 2):

| | pinball loss | typical miss | 90% coverage |
|---|---|---|---|
| This forecaster | 0.20 | ~36% | 0.87 |
| House persistence baseline | 0.387 | 82.8% | 0.783 |

Leave-one-out over the 12 rolling comparables: loss 0.243 (persistence
baseline: 0.357 on the same targets).

## License

MIT — see `LICENSE`.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
