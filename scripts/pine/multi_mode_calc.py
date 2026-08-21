# -*- coding: utf-8 -*-
"""Python port of Multi-Mode Indicator (live source == local git copy, confirmed
identical via pine_open+pine_get_source, no staleness issue unlike spread-exhaustion).

Unlike the other 3 MTF scripts, this one has NO request.security/cross-TF
component -- every series is self-referential to whichever timeframe's OHLC you
feed in. Run once per target timeframe, no as-of joining needed.

VOLUME CAVEAT: VWAP, close_vwap, ema_vol_200/absorption, and the body-engulf
box's volume gate all depend on `volume`. NIFTY (the index) has no usable
volume feed in our BigQuery/Kite sources (same issue as OBV -- see session
notes). These are still computed here from whatever volume column is present
so the code is correct and reusable for any stock with a real volume feed,
but for NIFTY specifically these columns will NOT match TradingView. Everything
else (EMAs, ADX(14,14), ATR, Bollinger Band, momentum, day-variables, ATH
bucket, EMA-spread percentiles, candle analysis) is volume-independent and
fully validated the same way as the other 3 scripts.

NOT ported: `fut_data`/`has_fo` (needs a separate NIFTY-futures continuous
series, out of scope) and pure chart-drawing elements (labels, the body-engulf
box's pixel coordinates, ATH-bucket label text).
"""
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, "scripts/pine")
from mtf_indicators import ema, rma, sma, stdev_pop, dmi_adx, atr_wilder


def percentile_rank_custom(src: np.ndarray, length: int) -> np.ndarray:
    """Multi-Mode's OWN inline percentile_rank() -- NOT ta.percentrank.
    `for i=0 to length-1: count += src[i]<=src`: includes the CURRENT bar as
    one of the `length` compared values (i=0 is offset 0 = current bar), uses
    <=. This differs from ta.percentrank (excludes current, uses <)."""
    n = len(src)
    out = np.full(n, np.nan)
    for i in range(length - 1, n):
        window = src[i - length + 1 : i + 1]
        if np.any(np.isnan(window)):
            continue
        out[i] = 100.0 * np.count_nonzero(window <= src[i]) / length
    return out


def calc_momentum(src: np.ndarray, n: int, lookback: int, smooth_len: int) -> np.ndarray:
    mom = np.empty(len(src))
    mom[:n] = np.nan
    mom[n:] = src[n:] - src[:-n]
    max_mom = pd.Series(mom).rolling(lookback).max().to_numpy()
    min_mom = pd.Series(mom).rolling(lookback).min().to_numpy()
    denom = max_mom - min_mom
    with np.errstate(invalid="ignore", divide="ignore"):
        norm = np.where(denom != 0, (mom - min_mom) / denom * 2 - 1, 0.0)
    norm[np.isnan(mom) | np.isnan(max_mom)] = np.nan
    return ema(np.nan_to_num(norm, nan=0.0), smooth_len)


def vwap_anchored(hlc3: np.ndarray, volume: np.ndarray, is_new_period: np.ndarray) -> np.ndarray:
    n = len(hlc3)
    out = np.full(n, np.nan)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        if is_new_period[i]:
            cum_pv = 0.0
            cum_v = 0.0
        cum_pv += hlc3[i] * volume[i]
        cum_v += volume[i]
        out[i] = cum_pv / cum_v if cum_v != 0 else np.nan
    return out


def multi_mode_calc(ohlc: pd.DataFrame, is_intraday: bool, bb_len=20, bb_mult=2.0,
                     day_length=30, adx_entry_min=14, adx_entry_max=30,
                     lookback_len=500) -> pd.DataFrame:
    ts = ohlc["timestamp"]
    o = ohlc["open"].to_numpy(dtype=float)
    h = ohlc["high"].to_numpy(dtype=float)
    l = ohlc["low"].to_numpy(dtype=float)
    c = ohlc["close"].to_numpy(dtype=float)
    v = ohlc["volume"].to_numpy(dtype=float) if "volume" in ohlc.columns else np.zeros(len(c))
    n = len(c)

    # --- core EMAs / ADX / ATR -------------------------------------------------
    ema5, ema9, ema20 = ema(c, 5), ema(c, 9), ema(c, 20)
    ema50, ema100, ema200 = ema(c, 50), ema(c, 100), ema(c, 200)
    ema_max = np.maximum.reduce([ema20, ema50, ema100, ema200])
    ema_min = np.minimum.reduce([ema20, ema50, ema100, ema200])

    diPlus, diMinus, _, adx = dmi_adx(h, l, c, 14, 14)
    atr_val = atr_wilder(h, l, c, 14)
    lt_atr = atr_wilder(h, l, c, 375)
    price = (o + h + c + l) / 4.0

    # --- EMA 5/9 cross condition + price-required-for-cross --------------------
    cross_up = (ema5 > ema9) & (np.roll(ema5, 1) <= np.roll(ema9, 1))
    cross_dn = (ema5 < ema9) & (np.roll(ema5, 1) >= np.roll(ema9, 1))
    cross_up[0] = cross_dn[0] = False
    ema_59_condition = np.where(cross_up, 1, np.where(cross_dn, -1, 0))

    dist_50 = np.abs(ema20 - ema50)
    dist_100 = np.abs(ema20 - ema100)
    dist_200 = np.abs(ema20 - ema200)
    nearest_ema = np.where((dist_50 <= dist_100) & (dist_50 <= dist_200), ema50,
                   np.where((dist_100 <= dist_50) & (dist_100 <= dist_200), ema100, ema200))
    next_price = np.maximum((21 * nearest_ema - 19 * ema20) / 2.0, 0.0)
    change_for_ema_20 = next_price - c

    alpha_fast, alpha_slow = 2.0 / 6, 2.0 / 10
    ema5_prev = np.roll(ema5, 1); ema5_prev[0] = np.nan
    ema9_prev = np.roll(ema9, 1); ema9_prev[0] = np.nan
    numerator = ema9_prev - ema5_prev + alpha_fast * ema5_prev - alpha_slow * ema9_prev
    price_for_cross = numerator / (alpha_fast - alpha_slow)
    points_required = price_for_cross - c

    # --- Bollinger Band on EMA20 ------------------------------------------------
    bb_basis = ema20
    bb_dev = bb_mult * stdev_pop(ema20, bb_len)
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev
    bandwidth = bb_upper - bb_lower
    bw_prev = np.roll(bandwidth, 1); bw_prev[0] = np.nan
    bl_prev = np.roll(bb_lower, 1); bl_prev[0] = np.nan
    bu_prev = np.roll(bb_upper, 1); bu_prev[0] = np.nan
    bb_up = bandwidth >= bw_prev
    bb_l_up, bb_l_down = bb_lower > bl_prev, bb_lower < bl_prev
    bb_u_up, bb_u_down = bb_upper > bu_prev, bb_upper < bu_prev

    band_color = np.full(n, "", dtype=object)
    band_color[bb_l_up & bb_u_up & bb_up] = "green_50"
    band_color[bb_l_up & bb_u_up & ~bb_up] = "green_80"
    band_color[bb_l_down & bb_u_down & bb_up] = "red_80"
    band_color[bb_l_down & bb_u_down & ~bb_up] = "red_50"
    band_color[bb_l_up & bb_u_down] = "fuchsia_80"
    band_color[bb_l_down & bb_u_up] = "aqua_80"
    ema_in_band = (ema_max <= bb_upper) & (ema_min >= bb_lower)

    # --- momentum ----------------------------------------------------------------
    momentum = calc_momentum(price, 5, 60, 9)
    mom_p1 = np.roll(momentum, 1); mom_p1[0] = np.nan
    mom_p2 = np.roll(momentum, 2); mom_p2[:2] = np.nan
    momentum_out = (momentum < mom_p1) & (mom_p1 < mom_p2) & (momentum > 0.5)

    # --- BB strategy entries/exits (intraday time gate) ---------------------------
    bb_adx_ok = (adx > adx_entry_min) & (adx < adx_entry_max)
    if is_intraday:
        hh = ts.dt.hour.to_numpy()
        mm = ts.dt.minute.to_numpy()
        in_time = ((hh > 9) | ((hh == 9) & (mm >= 30))) & ((hh < 15) | ((hh == 15) & (mm <= 2)))
    else:
        in_time = np.zeros(n, dtype=bool)
    allow_trade = in_time & bb_adx_ok

    is_fuchsia = band_color == "fuchsia_80"
    is_green = (band_color == "green_50") | (band_color == "green_80")
    is_red = (band_color == "red_50") | (band_color == "red_80")
    was_fuchsia = np.roll(is_fuchsia, 1); was_fuchsia[0] = False

    lowest_prev3 = pd.Series(np.roll(l, 1)).rolling(3).min().to_numpy()
    max_sl = np.maximum(lowest_prev3, ema20)

    long_entry = was_fuchsia & is_green & allow_trade & ~ema_in_band & (momentum > 0)
    short_entry = was_fuchsia & is_red & allow_trade & ~ema_in_band & (momentum < 0)

    last_trade = np.zeros(n, dtype=int)
    state = 0
    for i in range(n):
        if long_entry[i]:
            state = 1
        elif short_entry[i]:
            state = -1
        last_trade[i] = state
    close_long = (last_trade == 1) & (is_red | is_fuchsia | (l < max_sl) | momentum_out)
    close_short = (last_trade == -1) & (is_green | is_fuchsia | (l > max_sl) | momentum_out)

    # --- candle analysis -----------------------------------------------------------
    rang1 = h - l
    top_wick = h - np.maximum(o, c)
    bottom_wick = np.minimum(o, c) - l
    with np.errstate(invalid="ignore", divide="ignore"):
        top_perc = np.where(rang1 > 0, top_wick / rang1 * 100, 0.0)
        bottom_perc = np.where(rang1 > 0, bottom_wick / rang1 * 100, 0.0)
    long_bottom = (bottom_perc > 80) & (rang1 > atr_val)
    long_top = (top_perc > 80) & (rang1 > atr_val)

    h_prev = np.roll(h, 1); h_prev[0] = np.nan
    l_prev = np.roll(l, 1); l_prev[0] = np.nan
    overlap_high = np.minimum(h, h_prev)
    overlap_low = np.maximum(l, l_prev)
    overlap_range = np.maximum(0, overlap_high - overlap_low)
    curr_range = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        overlap_percent = np.where(curr_range > 0, overlap_range / curr_range, 0.0)
    is_overlap_50 = overlap_percent <= 0.3

    highest_ema = np.maximum.reduce([ema5, ema9, ema20, ema50, ema100, ema200])
    lowest_ema = np.minimum.reduce([ema5, ema9, ema20, ema50, ema100, ema200])
    covers_all_emas = (h >= highest_ema) & (l <= lowest_ema)

    # body-engulf count (price-only; the box-drawing volume gate is NOT applied here)
    max_lookback, min_engulf = 8, 3
    body_engulf_count = np.zeros(n, dtype=int)
    for i in range(n):
        cnt = 0
        oi, ci = o[i], c[i]
        for k in range(1, max_lookback + 1):
            if i - k < 0:
                break
            prev_body_high = max(o[i - k], c[i - k])
            prev_body_low = min(o[i - k], c[i - k])
            atrk = atr_val[i] if not np.isnan(atr_val[i]) else 0.0
            if max(oi + atrk * 0.2, ci + atrk * 0.2) >= prev_body_high and \
               min(oi - atrk * 0.2, ci - atrk * 0.2) <= prev_body_low:
                cnt += 1
            else:
                break
        body_engulf_count[i] = cnt

    # --- day variables (intraday only) ---------------------------------------------
    day_high = np.full(n, np.nan)
    day_low = np.full(n, np.nan)
    day_open = np.full(n, np.nan)
    first15_high = np.full(n, np.nan)
    first15_low = np.full(n, np.nan)
    if is_intraday:
        dates = ts.dt.date.to_numpy()
        mins_from_open = (ts.dt.hour.to_numpy() * 60 + ts.dt.minute.to_numpy()) - (9 * 60 + 15)
        dh = dl = do = np.nan
        f15h = f15l = np.nan
        prev_date = None
        for i in range(n):
            if dates[i] != prev_date:
                dh, dl, do = h[i], l[i], o[i]
                f15h = f15l = np.nan
                prev_date = dates[i]
            else:
                dh, dl = max(dh, h[i]), min(dl, l[i])
            if 0 <= mins_from_open[i] < 15:
                f15h = h[i] if np.isnan(f15h) else max(f15h, h[i])
                f15l = l[i] if np.isnan(f15l) else min(f15l, l[i])
            day_high[i], day_low[i], day_open[i] = dh, dl, do
            if mins_from_open[i] >= 15:
                first15_high[i], first15_low[i] = f15h, f15l
    day50 = (day_high + day_low) / 2.0
    high_30m = pd.Series(h).rolling(day_length).max().to_numpy()
    low_30m = pd.Series(l).rolling(day_length).min().to_numpy()
    high_5m = pd.Series(h).rolling(5).max().to_numpy()
    low_5m = pd.Series(l).rolling(5).min().to_numpy()
    mid_5m = (high_5m + low_5m) / 2.0
    mid_30m = (high_30m + low_30m) / 2.0
    m5p = np.roll(mid_5m, 1); m5p[0] = np.nan
    m30p = np.roll(mid_30m, 1); m30p[0] = np.nan
    mid_cross = ((mid_5m > mid_30m) & (m5p <= m30p)) | ((mid_5m < mid_30m) & (m5p >= m30p))

    # --- VWAP (volume-dependent -- see module docstring) ----------------------------
    hlc3 = (h + l + c) / 3.0
    if is_intraday:
        dates = ts.dt.date.to_numpy()
        is_new_period = np.zeros(n, dtype=bool)
        is_new_period[0] = True
        is_new_period[1:] = dates[1:] != dates[:-1]
    else:
        is_new_period = np.ones(n, dtype=bool)  # D/W/M: resets every bar (matches Pine)
    vwap_value = vwap_anchored(hlc3, v, is_new_period)

    close_vwap = np.full(n, np.nan)
    if is_intraday:
        hh = ts.dt.hour.to_numpy(); mm = ts.dt.minute.to_numpy()
        is_close_window = (hh == 15) & (mm <= 30)
        cv_sum = v_sum = 0.0
        prev_date = None
        for i in range(n):
            if dates[i] != prev_date:
                cv_sum = v_sum = 0.0
                prev_date = dates[i]
            if is_close_window[i]:
                cv_sum += hlc3[i] * v[i]
                v_sum += v[i]
            close_vwap[i] = cv_sum / v_sum if v_sum != 0 else np.nan

    # --- reference candle (t-2) high/low break --------------------------------------
    h_t2 = pd.Series(h).shift(2).to_numpy()
    l_t2 = pd.Series(l).shift(2).to_numpy()
    h1 = np.roll(h, 1); h1[0] = np.nan
    h2 = np.roll(h, 2); h2[:2] = np.nan
    l1 = np.roll(l, 1); l1[0] = np.nan
    l2 = np.roll(l, 2); l2[:2] = np.nan
    no_high_break = (h <= h_t2) & (h1 <= h_t2) & (h2 <= h_t2) & (ema5 > ema9)
    no_low_break = (l >= l_t2) & (l1 >= l_t2) & (l2 >= l_t2) & (ema5 < ema9)

    # --- volume-based absorption (see module docstring) -----------------------------
    ema_vol_200 = ema(v, 200)
    ema_atr_200 = ema(atr_val, 200)
    absorption = (v > 1.3 * ema_vol_200) & (atr_val < 0.8 * ema_atr_200)

    mid = (h + l) / 2.0

    # --- ATH bucket (running all-time high) -----------------------------------------
    true_ath = np.maximum.accumulate(np.where(np.isnan(h), -np.inf, h))
    true_ath = np.where(np.isinf(true_ath), np.nan, true_ath)

    # --- EMA stack / candle-position pattern -----------------------------------------
    ema_stack = (ema200 > ema20) & (ema20 > ema100) & (ema100 > ema50)
    h1c = np.roll(h, 1); h1c[0] = np.nan
    h2c = np.roll(h, 2); h2c[:2] = np.nan
    e50_1 = np.roll(ema50, 1); e50_1[0] = np.nan
    e50_2 = np.roll(ema50, 2); e50_2[:2] = np.nan
    candle_position = (h < ema50) & (h1c < e50_1) & (h2c <= e50_2)
    match_cond = ema_stack & candle_position

    has_full_ema = ~pd.Series(ema200).isna().rolling(300).max().fillna(1).astype(bool).to_numpy()
    # simpler/more literal: True once the trailing 300 bars all have a non-na ema200
    valid200 = (~np.isnan(ema200)).astype(int)
    trailing_valid_300 = pd.Series(valid200).rolling(300).sum().to_numpy()
    has_full_ema = trailing_valid_300 >= 300

    # --- EMA-spread / price-spread percentiles ---------------------------------------
    with np.errstate(invalid="ignore", divide="ignore"):
        ema_spread_raw = (ema_max - ema_min) / ema_min * 100.0
        price_spread_raw = (c - ema200) / ema200 * 100.0
    ema_spread_percent = np.round(ema(ema_spread_raw, 5), 3)
    price_spread_percent = np.round(ema(price_spread_raw, 5), 3)

    ema_spread_percentile = percentile_rank_custom(ema_spread_percent, lookback_len)
    price_spread_input = np.abs(price_spread_percent)
    price_to_ema200_percentile = percentile_rank_custom(price_spread_input, lookback_len)
    very_high_low = (ema_spread_percentile > 85) & (price_to_ema200_percentile > 90)
    too_much_consolidation = (ema_spread_percentile < 15) & \
        ((price_to_ema200_percentile < 5) | ((c < ema_max) & (c > ema_min)))

    max_price_spread = pd.Series(price_spread_input).rolling(lookback_len).max().to_numpy()
    current_price_spread = np.abs(c - ema200) / ema200 * 100.0
    price_spread_to_100p = (max_price_spread - current_price_spread) * ema200 / 100.0

    # --- 5-bar lower-highs + EMA-stack pattern counter (cumulative) -------------------
    h3 = np.roll(h, 3); h3[:3] = np.nan
    h4 = np.roll(h, 4); h4[:4] = np.nan
    h5 = np.roll(h, 5); h5[:5] = np.nan
    price_cond = (h < h1) & (h1 < h2) & (h2 < h3) & (h3 < h4) & (h4 < h5)
    ema_cond = (ema200 > ema20) & (ema20 > ema100) & (ema100 > ema50)
    cond = price_cond & ema_cond
    total_count = np.cumsum(cond.astype(int))

    return pd.DataFrame({
        "timestamp": ts.to_numpy(),
        "ema5": ema5, "ema9": ema9, "ema20": ema20, "ema50": ema50, "ema100": ema100, "ema200": ema200,
        "ema_59_condition": ema_59_condition, "change_for_ema_20": change_for_ema_20,
        "points_required_5_9_cross": points_required,
        "diPlus14": diPlus, "diMinus14": diMinus, "adx14": adx,
        "atr14": atr_val, "atr375": lt_atr,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "bandwidth": bandwidth, "bandColor": band_color,
        "ema_in_band": ema_in_band,
        "momentum": momentum, "momentum_out": momentum_out,
        "bb_adx_ok": bb_adx_ok, "allow_trade": allow_trade,
        "longEntry": long_entry, "shortEntry": short_entry, "lastTrade": last_trade,
        "closeLong": close_long, "closeShort": close_short,
        "top_wick_pct": top_perc, "bottom_wick_pct": bottom_perc,
        "long_bottom": long_bottom, "long_top": long_top,
        "overlap_percent": overlap_percent, "is_overlap_50": is_overlap_50,
        "coversAllEMAs": covers_all_emas, "bodyEngulfCount": body_engulf_count,
        "dayHigh": day_high, "dayLow": day_low, "dayOpen": day_open, "day50": day50,
        "high_30": high_30m, "low_30": low_30m, "high_5": high_5m, "low_5": low_5m,
        "mid_5m": mid_5m, "mid_30m": mid_30m, "midCross": mid_cross,
        "first15High": first15_high, "first15Low": first15_low,
        "VWAP": vwap_value, "close_vwap": close_vwap,
        "no_high_break": no_high_break, "no_low_break": no_low_break,
        "plot_high": np.where(no_high_break, h_t2, np.nan), "plot_low": np.where(no_low_break, l_t2, np.nan),
        "ema_vol_200": ema_vol_200, "ema_atr_200": ema_atr_200, "absorption": absorption,
        "mid": mid, "true_ath": true_ath,
        "ema_stack": ema_stack, "candle_position": candle_position, "match": match_cond,
        "hasFullEMA": has_full_ema,
        "ema_spread_percent": ema_spread_percent, "price_spread_percent": price_spread_percent,
        "ema_spread_percentile": ema_spread_percentile, "price_to_ema200_percentile": price_to_ema200_percentile,
        "very_high_low": very_high_low, "too_much_consolidation": too_much_consolidation,
        "price_spread_to_100p": price_spread_to_100p,
        "totalCount_5barLH_stack": total_count,
    })
