# -*- coding: utf-8 -*-
"""Python port of the live (non-git-stale, pulled directly from the TradingView
Pine editor via pine_open+pine_get_source) MTF indicator scripts in this folder:

  - mtf_spread_exhaustion.pine   (v83, "SpreadExhaust Dir")
  - mtf_adx_indicator.pine       ("MTF adx Bees to Elephant" incl. ADX Bowl detector)
  - mtf_rsi_indicator.pine       ("MTF RSI Bees to Elephant" -- same f_calc as ADX script)

OBV and Multi-Mode Indicator are NOT ported here (OBV: NIFTY has no usable volume feed
in our BigQuery/Kite sources; Multi-Mode: separate, much larger port, phase 2).

Formulas match Pine semantics:
  - ta.ema: recursive EMA, alpha=2/(len+1), seeded ema[0]=src[0].
  - ta.rma: Wilder RMA, alpha=1/len, seeded with SMA of the first `len` values.
  - ta.rsi: RMA-smoothed avg gain/loss -> 100-100/(1+RS).
  - ta.dmi: standard Wilder DMI/ADX (+DI/-DI via RMA(DM,len), ADX = RMA(DX,len)).
  - ta.percentrank(src,len): % of the PRIOR `len` bars (excluding current) strictly
    less than the current value.

Resampling is session-anchored (09:15 IST start), matching how TradingView builds
intraday N-minute bars for NSE index charts -- NOT wall-clock-aligned. Daily/Weekly/
Monthly use calendar grouping (Mon-Fri trading week).
"""
import numpy as np
import pandas as pd


# ============================ Pine primitives ============================

def ema(src: np.ndarray, length: int) -> np.ndarray:
    """Matches Pine's actual ta.ema recursion: sum := na(sum[1]) ? src :
    alpha*src+(1-alpha)*sum[1]. Re-seeds on the first non-NaN src bar instead of
    a fixed index -- critical when src itself has a NaN warm-up prefix (e.g.
    ema(rsi(...), 8)), otherwise a single leading NaN poisons the whole series."""
    alpha = 2.0 / (length + 1)
    n = len(src)
    out = np.full(n, np.nan)
    prev = np.nan
    for i in range(n):
        out[i] = src[i] if np.isnan(prev) else alpha * src[i] + (1 - alpha) * prev
        prev = out[i]
    return out


def rma(src: np.ndarray, length: int) -> np.ndarray:
    """Matches Pine's actual ta.rma recursion: sum := na(sum[1]) ? sma(src,length)
    : alpha*src+(1-alpha)*sum[1]. Re-seeds via a rolling SMA (NaN until a full
    non-NaN trailing window exists) instead of a fixed index -- same NaN-warm-up
    fix as ema() above, needed for nested calls like rma(dx, adxLen)."""
    n = len(src)
    out = np.full(n, np.nan)
    if n < length:
        return out
    alpha = 1.0 / length
    seed = pd.Series(src).rolling(length).mean().to_numpy()
    prev = np.nan
    for i in range(n):
        out[i] = seed[i] if np.isnan(prev) else alpha * src[i] + (1 - alpha) * prev
        prev = out[i]
    return out


def sma(src: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(src).rolling(length).mean().to_numpy()


def stdev_pop(src: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(src).rolling(length).std(ddof=0).to_numpy()


def rsi_wilder(close: np.ndarray, length: int) -> np.ndarray:
    n = len(close)
    change = np.empty(n)
    change[0] = 0.0
    change[1:] = close[1:] - close[:-1]
    up = np.maximum(change, 0.0)
    down = -np.minimum(change, 0.0)
    up_r = rma(up, length)
    down_r = rma(down, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up_r / down_r
        out = np.where(down_r == 0, 100.0, np.where(up_r == 0, 0.0, 100.0 - 100.0 / (1.0 + rs)))
    out[: length] = np.nan
    return out


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    hl = high[1:] - low[1:]
    hc = np.abs(high[1:] - close[:-1])
    lc = np.abs(low[1:] - close[:-1])
    tr[1:] = np.maximum(hl, np.maximum(hc, lc))
    return rma(tr, length)


def dmi_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, di_len: int, adx_len: int):
    """Returns (pDI, mDI, dx, adx) -- matches ta.dmi(diLen, adxLen)."""
    n = len(high)
    up = np.empty(n)
    dn = np.empty(n)
    up[0] = 0.0
    dn[0] = 0.0
    up[1:] = high[1:] - high[:-1]
    dn[1:] = low[:-1] - low[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    hl = high[1:] - low[1:]
    hc = np.abs(high[1:] - close[:-1])
    lc = np.abs(low[1:] - close[:-1])
    tr[1:] = np.maximum(hl, np.maximum(hc, lc))

    sP = rma(plus_dm, di_len)
    sM = rma(minus_dm, di_len)
    sT = rma(tr, di_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        pDI = np.where(sT == 0, 0.0, 100.0 * sP / sT)
        mDI = np.where(sT == 0, 0.0, 100.0 * sM / sT)
        dsum = pDI + mDI
        dx = np.where(dsum == 0, 0.0, 100.0 * np.abs(pDI - mDI) / dsum)
    # propagate the RMA warm-up NaNs (np.where with nan comparisons above can turn
    # NaN into 0.0 via the `sT==0` branch since nan==0 is False -> takes false branch
    # with nan/nan = nan anyway, so nan already propagates correctly through the true
    # numpy division; the explicit warm-up mask below is just a safety net)
    warm = np.isnan(sT)
    pDI[warm] = np.nan
    mDI[warm] = np.nan
    dx[warm] = np.nan
    adx = rma(dx, adx_len)
    return pDI, mDI, dx, adx


def percentrank_pine(src: np.ndarray, length: int) -> np.ndarray:
    """ta.percentrank(source, length): % of the prior `length` bars (excluding
    current) strictly less than the current value."""
    n = len(src)
    out = np.full(n, np.nan)
    for i in range(length, n):
        window = src[i - length : i]
        out[i] = 100.0 * np.count_nonzero(window < src[i]) / length
    return out


# ============================ Resampling (session-anchored) ============================

SESSION_START_MIN = 9 * 60 + 15  # 09:15 IST


def resample_intraday(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Session-anchored N-minute bars from 1-min OHLC (matches TV's intraday
    resampling for NSE: buckets start fresh at 09:15 each day, last bar of the
    day is a partial bucket)."""
    d = df1m.copy()
    d["date"] = d["timestamp"].dt.date
    mins = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["bucket"] = (mins - SESSION_START_MIN) // minutes
    g = d.groupby(["date", "bucket"], sort=True, as_index=False)
    out = g.agg(timestamp=("timestamp", "first"), open=("open", "first"),
                high=("high", "max"), low=("low", "min"), close=("close", "last"))
    out = out.sort_values("timestamp").reset_index(drop=True)
    return out[["timestamp", "open", "high", "low", "close"]]


def resample_daily(df1m: pd.DataFrame) -> pd.DataFrame:
    d = df1m.copy()
    d["date"] = d["timestamp"].dt.date
    out = d.groupby("date", as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    out["timestamp"] = pd.to_datetime(out["date"])
    return out[["timestamp", "open", "high", "low", "close"]]


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.set_index("timestamp")
    out = d.resample("W-FRI", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return out


def resample_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.set_index("timestamp")
    out = d.resample("MS").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return out


# ============================ Indicator: MTF Spread-Exhaustion (v83) ============================

def spread_exhaustion_calc(ohlc: pd.DataFrame, l1=20, l2=50, l3=100, l4=200, pr_len=200,
                            sig_len=5, atr_len=14, atr_k=1.0, dmi_len=8, rsi_len=8,
                            use_band_sign=True) -> pd.DataFrame:
    """Per-TF f_calc() from the live v83 script. `ohlc` is ONE timeframe's own
    resampled OHLC (this function is called once per target timeframe)."""
    close = ohlc["close"].to_numpy(dtype=float)
    high = ohlc["high"].to_numpy(dtype=float)
    low = ohlc["low"].to_numpy(dtype=float)

    e1 = ema(close, l1)
    e2 = ema(close, l2)
    e3 = ema(close, l3)
    e4 = ema(close, l4)
    mx = np.maximum.reduce([e1, e2, e3, e4])
    mn = np.minimum.reduce([e1, e2, e3, e4])
    fan = (mx - mn) / e4 * 100.0
    mag_pct = percentrank_pine(fan, pr_len)

    trig = sma(close, sig_len)
    atr_val = atr_wilder(high, low, close, atr_len)
    band = atr_val * atr_k

    n = len(close)
    band_sgn = np.empty(n)
    band_sgn[0] = 1.0
    for i in range(1, n):
        if trig[i] > mx[i] + band[i]:
            band_sgn[i] = 1.0
        elif trig[i] < mn[i] - band[i]:
            band_sgn[i] = -1.0
        else:
            band_sgn[i] = band_sgn[i - 1]

    sgn = band_sgn if use_band_sign else np.where(e1 >= e4, 1.0, -1.0)
    signed_pct = sgn * mag_pct
    signed_fan = sgn * fan

    rsi_raw = rsi_wilder(close, rsi_len)
    rsi2 = ema(rsi_raw, 8)
    _, _, _, adx = dmi_adx(high, low, close, dmi_len, dmi_len)

    return pd.DataFrame({
        "timestamp": ohlc["timestamp"].to_numpy(),
        "signedPct": signed_pct, "signedFan": signed_fan, "rsi2": rsi2, "adx": adx,
    })


# ============================ Indicator: MTF ADX/RSI shared f_calc ============================

def mtf_rsi_adx_calc(ohlc: pd.DataFrame, dmi_len=8, rsi_len=8) -> pd.DataFrame:
    """Shared f_calc() from mtf_adx_indicator.pine / mtf_rsi_indicator.pine:
    rsi2 = EMA(RSI(close,rsiLen),8); adx = ADX(dmiLen,dmiLen)."""
    close = ohlc["close"].to_numpy(dtype=float)
    high = ohlc["high"].to_numpy(dtype=float)
    low = ohlc["low"].to_numpy(dtype=float)
    rsi_raw = rsi_wilder(close, rsi_len)
    rsi2 = ema(rsi_raw, 8)
    _, _, _, adx = dmi_adx(high, low, close, dmi_len, dmi_len)
    return pd.DataFrame({"timestamp": ohlc["timestamp"].to_numpy(), "rsi2": rsi2, "adx": adx})


# ============================ Indicator: current-TF ADX Bowl/Elephant detector ============================

def adx_bowl_detector(ohlc: pd.DataFrame, dmi_len=8, bowl_norm_length=100, bowl_smooth=3,
                       bowl_min_rise=1.0, expansion_adx=20.0, elephant_adx=23.0,
                       bowl_max_adx=25.0, bowl_min_depth=0.08, pivot_short=2, pivot_long=5,
                       di_difference=2.0, use_di_filter=True) -> pd.DataFrame:
    """f_dmi_state() + the full ADX Bowl/Expansion/Elephant state machine from
    mtf_adx_indicator.pine, run on `ohlc` treated as the chart's OWN (current) TF."""
    high = ohlc["high"].to_numpy(dtype=float)
    low = ohlc["low"].to_numpy(dtype=float)
    close = ohlc["close"].to_numpy(dtype=float)
    n = len(high)

    pDI, mDI, dx, adx_cur = dmi_adx(high, low, close, dmi_len, dmi_len)

    adx_slope_raw = np.diff(adx_cur, prepend=adx_cur[0])
    adx_slope_raw[0] = np.nan
    adx_slope = ema(np.nan_to_num(adx_slope_raw, nan=0.0), bowl_smooth)

    adx_rising2 = np.zeros(n, dtype=bool)
    adx_falling = np.zeros(n, dtype=bool)
    for i in range(2, n):
        if not (np.isnan(adx_cur[i]) or np.isnan(adx_cur[i - 1]) or np.isnan(adx_cur[i - 2])):
            adx_rising2[i] = adx_cur[i] > adx_cur[i - 1] > adx_cur[i - 2]
        if not (np.isnan(adx_cur[i]) or np.isnan(adx_cur[i - 1])):
            adx_falling[i] = adx_cur[i] < adx_cur[i - 1]

    adx_lowest = pd.Series(adx_cur).rolling(bowl_norm_length).min().to_numpy()
    adx_highest = pd.Series(adx_cur).rolling(bowl_norm_length).max().to_numpy()
    denom = adx_highest - adx_lowest
    with np.errstate(divide="ignore", invalid="ignore"):
        adx_norm = np.where(denom != 0, (adx_cur - adx_lowest) / denom, 0.5)
    adx_norm = np.clip(adx_norm, 0.0, 1.0)
    adx_norm[np.isnan(adx_cur)] = np.nan

    def pivot_low(arr, left, right):
        out = np.full(len(arr), np.nan)
        for i in range(left, len(arr) - right):
            window = arr[i - left : i + right + 1]
            if np.any(np.isnan(window)):
                continue
            if arr[i] == window.min() and np.sum(window == arr[i]) == 1:
                out[i] = arr[i]
        return out

    def pivot_high(arr, left, right):
        out = np.full(len(arr), np.nan)
        for i in range(left, len(arr) - right):
            window = arr[i - left : i + right + 1]
            if np.any(np.isnan(window)):
                continue
            if arr[i] == window.max() and np.sum(window == arr[i]) == 1:
                out[i] = arr[i]
        return out

    piv_low_s = pivot_low(adx_norm, pivot_short, pivot_short)
    piv_low_l = pivot_low(adx_norm, pivot_long, pivot_long)
    piv_high_s = pivot_high(adx_norm, pivot_short, pivot_short)
    piv_high_l = pivot_high(adx_norm, pivot_long, pivot_long)

    bull_di = (pDI > mDI) & ((pDI - mDI) >= di_difference)
    bear_di = (mDI > pDI) & ((mDI - pDI) >= di_difference)

    bowl_active = np.zeros(n, dtype=bool)
    bowl_start_adx = np.full(n, np.nan)
    bull_expansion = np.zeros(n, dtype=bool)
    bear_expansion = np.zeros(n, dtype=bool)
    bull_elephant = np.zeros(n, dtype=bool)
    bear_elephant = np.zeros(n, dtype=bool)
    bull_bowl_active = np.zeros(n, dtype=bool)
    bear_bowl_active = np.zeros(n, dtype=bool)

    active = False
    start_adx = np.nan
    for i in range(max(pivot_long, bowl_norm_length), n):
        # pivots at bar i confirm `offset` bars in the past (i - offset)
        bull_pivot = None
        offset = None
        if i - pivot_short >= 0 and not np.isnan(piv_low_s[i]):
            bull_pivot = piv_low_s[i]
            offset = pivot_short
        elif i - pivot_long >= 0 and not np.isnan(piv_low_l[i]):
            bull_pivot = piv_low_l[i]
            offset = pivot_long

        if bull_pivot is not None:
            confirmed_idx = i - offset
            pivot_adx = adx_cur[confirmed_idx] if confirmed_idx >= 0 else np.nan
            depth = adx_norm[i] - bull_pivot
            if (not np.isnan(pivot_adx)) and pivot_adx <= bowl_max_adx and depth >= bowl_min_depth:
                active = True
                start_adx = pivot_adx

        adx_peak = (not np.isnan(piv_high_s[i])) or (not np.isnan(piv_high_l[i]))
        if active and adx_peak and adx_cur[i] >= elephant_adx:
            active = False
        if active and not np.isnan(start_adx) and adx_cur[i] < start_adx:
            active = False

        bowl_active[i] = active
        bowl_start_adx[i] = start_adx if active else np.nan

        bull_active_i = active and bull_di[i]
        bear_active_i = active and bear_di[i]
        bull_bowl_active[i] = bull_active_i
        bear_bowl_active[i] = bear_active_i

        rise = adx_cur[i] - start_adx if (active and not np.isnan(start_adx)) else np.nan
        bull_rise = rise if bull_active_i else np.nan
        bear_rise = rise if bear_active_i else np.nan

        if bull_active_i and (not np.isnan(bull_rise)) and bull_rise >= bowl_min_rise and \
           adx_cur[i] >= expansion_adx and adx_slope[i] > 0 and (not use_di_filter or bull_di[i]):
            bull_expansion[i] = True
        if bear_active_i and (not np.isnan(bear_rise)) and bear_rise >= bowl_min_rise and \
           adx_cur[i] >= expansion_adx and adx_slope[i] > 0 and (not use_di_filter or bear_di[i]):
            bear_expansion[i] = True

        if bull_active_i and adx_cur[i] >= elephant_adx and adx_rising2[i] and (not use_di_filter or bull_di[i]):
            bull_elephant[i] = True
        if bear_active_i and adx_cur[i] >= elephant_adx and adx_rising2[i] and (not use_di_filter or bear_di[i]):
            bear_elephant[i] = True

    return pd.DataFrame({
        "timestamp": ohlc["timestamp"].to_numpy(),
        "pDI": pDI, "mDI": mDI, "dx": dx, "adxCur": adx_cur, "adxSlope": adx_slope,
        "adxNorm": adx_norm, "bowlActive": bowl_active, "bullBowlActive": bull_bowl_active,
        "bearBowlActive": bear_bowl_active, "bullExpansion": bull_expansion,
        "bearExpansion": bear_expansion, "bullElephant": bull_elephant, "bearElephant": bear_elephant,
    })
