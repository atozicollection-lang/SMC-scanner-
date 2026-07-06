import requests
import time
from datetime import datetime

TELEGRAM_TOKEN = "8917592730:AAGWhc54aQh5hK-Hj3T0lDzaAd_c-IiWkN4"
TELEGRAM_CHAT_ID = "807175476"
BINANCE_API = "https://api.binance.com/api/v3"
SCAN_INTERVAL = 300  # 5 minutes
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
TOP_N = 10
notified = set()

# ── TELEGRAM ──────────────────────────────────────────────────────────
def send_telegram(text):
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        return res.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ── FETCH ALL 500+ PAIRS ──────────────────────────────────────────────
def fetch_all_pairs():
    try:
        print(f"[{now()}] Fetching ALL available Binance markets...")

        # Get exchange info for all valid trading pairs
        exch_res = requests.get(f"{BINANCE_API}/exchangeInfo", timeout=20)
        all_symbols = exch_res.json()["symbols"]

        # Get 24hr ticker for volume data
        ticker_res = requests.get(f"{BINANCE_API}/ticker/24hr", timeout=20)
        ticker_data = {t["symbol"]: t for t in ticker_res.json()}

        pairs = []
        for s in all_symbols:
            sym = s["symbol"]
            # Only USDT pairs, active, no leveraged tokens
            if (s["status"] != "TRADING"): continue
            if not sym.endswith("USDT"): continue
            if any(x in sym for x in ["UP","DOWN","BULL","BEAR"]): continue

            # Must have some volume
            vol = float(ticker_data.get(sym, {}).get("quoteVolume", 0))
            if vol < 100_000: continue  # very low minimum to catch all markets

            pairs.append({
                "symbol": sym,
                "volume": vol,
                "change": float(ticker_data.get(sym, {}).get("priceChangePercent", 0))
            })

        # Sort by volume
        pairs.sort(key=lambda x: x["volume"], reverse=True)

        symbols = [p["symbol"] for p in pairs]
        print(f"[{now()}] ✅ Loaded {len(symbols)} pairs (ALL markets — Crypto + bStocks + Commodities)")
        return symbols, pairs

    except Exception as e:
        print(f"[{now()}] Error fetching pairs: {e}")
        fallback = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT",
                    "XAUTUSDT","PAXGUSDT","SPCXBUSDT","TSLABUSDT"]
        return fallback, []

# ── FETCH CANDLES ─────────────────────────────────────────────────────
def fetch_candles(symbol, interval, limit=150):
    try:
        res = requests.get(f"{BINANCE_API}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10)
        data = res.json()
        if not isinstance(data, list): return []
        return [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),
                 "c":float(k[4]),"v":float(k[5])} for k in data]
    except:
        return []

# ── RSI ───────────────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    g, l = 0, 0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i-1]
        if d >= 0: g += d
        else: l -= d
    ag, al = g/period, l/period
    if al == 0: return 100.0
    return round(100 - 100/(1 + ag/al), 2)

# ── SWING LOWS ────────────────────────────────────────────────────────
def find_swing_lows(candles, lookback=3):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        c = candles[i]
        left  = all(candles[j]["l"] >= c["l"] for j in range(i-lookback, i))
        right = all(candles[j]["l"] >= c["l"] for j in range(i+1, i+lookback+1))
        if left and right:
            swings.append((i, c["l"]))
    return swings

# ── VOLUME RATIO ──────────────────────────────────────────────────────
def calc_volume_ratio(candles, idx, lookback=20):
    start = max(0, idx - lookback)
    avg = sum(c["v"] for c in candles[start:idx]) / max(1, idx - start)
    if avg == 0: return 1.0
    return round(candles[idx]["v"] / avg, 2)

# ── CONFIDENCE SCORE ──────────────────────────────────────────────────
def calc_confidence(s):
    score = 0
    reasons = []

    # RSI divergence gap (max 30 pts)
    rsi_gap = s["leg2_rsi"] - s["leg1_rsi"]
    if rsi_gap >= 15:
        score += 30; reasons.append(f"✅ Strong RSI divergence: +{rsi_gap:.1f}")
    elif rsi_gap >= 10:
        score += 20; reasons.append(f"✅ Good RSI divergence: +{rsi_gap:.1f}")
    elif rsi_gap >= 5:
        score += 10; reasons.append(f"⚠️ Moderate RSI divergence: +{rsi_gap:.1f}")
    else:
        score += 3; reasons.append(f"🔸 Weak RSI divergence: +{rsi_gap:.1f}")

    # Leg1 RSI depth (max 20 pts)
    if s["leg1_rsi"] <= 15:
        score += 20; reasons.append(f"✅ Extreme oversold Leg1: {s['leg1_rsi']}")
    elif s["leg1_rsi"] <= 20:
        score += 15; reasons.append(f"✅ Very oversold Leg1: {s['leg1_rsi']}")
    elif s["leg1_rsi"] <= 25:
        score += 10; reasons.append(f"✅ Oversold Leg1: {s['leg1_rsi']}")
    else:
        score += 5;  reasons.append(f"⚠️ Leg1 RSI: {s['leg1_rsi']}")

    # Sweep size (max 15 pts)
    sw = s["sweep_pct"]
    if sw >= 1.0:
        score += 15; reasons.append(f"✅ Large sweep: {sw}%")
    elif sw >= 0.5:
        score += 10; reasons.append(f"✅ Good sweep: {sw}%")
    elif sw >= 0.2:
        score += 5;  reasons.append(f"⚠️ Small sweep: {sw}%")
    else:
        score += 2;  reasons.append(f"🔸 Tiny sweep: {sw}%")

    # Body rejection (max 15 pts)
    rej = ((s["leg2_close"] - s["leg1_low"]) / s["leg1_low"]) * 100
    s["rejection_pct"] = round(rej, 3)
    if rej >= 1.0:
        score += 15; reasons.append(f"✅ Strong rejection: +{rej:.2f}%")
    elif rej >= 0.5:
        score += 10; reasons.append(f"✅ Good rejection: +{rej:.2f}%")
    else:
        score += 5;  reasons.append(f"⚠️ Weak rejection: +{rej:.2f}%")

    # Volume spike (max 10 pts)
    vr = s.get("vol_ratio", 1.0)
    if vr >= 2.0:
        score += 10; reasons.append(f"✅ Strong volume: {vr}x")
    elif vr >= 1.5:
        score += 7;  reasons.append(f"✅ Good volume: {vr}x")
    elif vr >= 1.2:
        score += 4;  reasons.append(f"⚠️ Slight volume: {vr}x")

    # Timeframe bonus (max 10 pts)
    tf_pts = {"1d":10,"4h":8,"1h":5,"15m":2}.get(s.get("tf","15m"), 2)
    score += tf_pts
    if tf_pts >= 8:
        reasons.append(f"✅ Higher TF ({s.get('tf','')}) = more reliable")

    # Bounce between legs (max 5 pts) — proves Leg1 was real
    bounce = s.get("bounce_pct", 0)
    if bounce >= 3.0:
        score += 5; reasons.append(f"✅ Strong bounce between legs: {bounce}%")
    elif bounce >= 1.0:
        score += 3; reasons.append(f"✅ Good bounce between legs: {bounce}%")

    # Bullish rejection candle on Leg2 (max 5 pts)
    if s.get("is_bullish_candle", False):
        score += 5; reasons.append(f"✅ Leg 2 is bullish candle (green close)")
    else:
        reasons.append(f"⚠️ Leg 2 closed bearish (weaker rejection)")

    # RSI gap must be >= 5 already enforced, but bonus for large gap

        score += 10; reasons.append(f"✅ RSI just crossed 30: {s['leg2_rsi']} (early entry)")
    elif 35 < s["leg2_rsi"] <= 42:
        score += 5;  reasons.append(f"⚠️ Leg2 RSI: {s['leg2_rsi']}")

    score = min(score, 100)
    if score >= 80:   label, emoji = "🔥 VERY HIGH", "🔥"
    elif score >= 65: label, emoji = "✅ HIGH", "✅"
    elif score >= 50: label, emoji = "⚠️ MODERATE", "⚠️"
    else:             label, emoji = "🔸 LOW", "🔸"

    return score, label, emoji, reasons

# ── DETECT SETUP ──────────────────────────────────────────────────────
def detect_setup(candles, tf="15m"):
    """
    STRICT Bullish RSI Divergence + Liquidity Sweep

    Rule 1 — Leg 1 (First Swing Low):
        - Must be a REAL swing low (lower than surrounding candles)
        - RSI at that candle MUST be strictly < 30
        - RSI must be the LOWEST point in that region (confirms deep oversold)

    Rule 2 — Leg 2 (Liquidity Sweep):
        - Price must make a LOWER LOW than Leg 1 (wick goes BELOW Leg 1 low)
        - The WICK of Leg 2 candle pierces below Leg 1 low
        - The BODY (both open AND close) of Leg 2 candle must be ABOVE Leg 1 low
        - Leg 2 must happen AFTER Leg 1 with at least 5 candles between them
        - Between Leg 1 and Leg 2 there must be a bounce (price went up then came back down)

    Rule 3 — RSI Divergence Confirmation:
        - RSI at Leg 2 MUST be strictly HIGHER than RSI at Leg 1
        - RSI at Leg 2 MUST be ABOVE 30 (closing out of oversold)
        - The divergence gap must be meaningful (at least 5 RSI points)

    Extra filters to prevent false signals:
        - Leg 2 candle must be a bullish rejection candle (close > open preferred)
        - The bounce between Leg 1 and Leg 2 must be at least 0.5% (real bounce, not noise)
        - Leg 2 wick below Leg 1 must be at least 0.1% (real sweep, not just touching)
    """
    if len(candles) < 60: return None

    closes = [c["c"] for c in candles]
    recent = candles[-120:]  # Look back further for better swing detection
    recent_closes = [c["c"] for c in recent]

    # Use stricter lookback for real swing lows
    swings = find_swing_lows(recent, lookback=4)
    if len(swings) < 2: return None

    for i in range(len(swings)-1):
        for j in range(i+1, len(swings)):
            leg1_idx, leg1_low = swings[i]
            leg2_idx, leg2_low = swings[j]

            # ── STRICT: Must have at least 5 candles between legs ────
            if leg2_idx - leg1_idx < 5: continue

            # ── STRICT: Must have a real bounce between Leg1 and Leg2 ─
            # Find the highest point between the two swing lows
            between = recent[leg1_idx:leg2_idx]
            if not between: continue
            highest_between = max(c["h"] for c in between)
            bounce_pct = (highest_between - leg1_low) / leg1_low * 100
            # Bounce must be at least 0.5% — proves it was a real Leg 1, not noise
            if bounce_pct < 0.5: continue

            # ── RULE 1: Leg1 RSI strictly < 30 ──────────────────────
            abs1 = len(candles) - 120 + leg1_idx
            if abs1 < 14: continue
            rsi1 = calc_rsi(closes[:abs1+1])
            if rsi1 is None or rsi1 >= 30: continue

            # ── RULE 2A: Leg2 wick MUST go BELOW Leg1 low ────────────
            l2c = recent[leg2_idx]
            if l2c["l"] >= leg1_low: continue  # No sweep if not below

            # ── RULE 2B: Sweep must be meaningful (at least 0.1%) ────
            sweep_pct = (leg1_low - l2c["l"]) / leg1_low * 100
            if sweep_pct < 0.1: continue  # Reject tiny accidental touches

            # ── RULE 2C: BODY must close ABOVE Leg1 low ──────────────
            # Both open AND close must be above Leg1 low
            body_low  = min(l2c["o"], l2c["c"])
            body_high = max(l2c["o"], l2c["c"])
            if body_low <= leg1_low: continue   # Body dipped below = not a sweep, it's breakdown
            if body_high <= leg1_low: continue  # Extra safety check

            # ── RULE 2D: Rejection candle preferred (bullish close) ───
            # Close should be above open (green candle = strong rejection)
            is_bullish_candle = l2c["c"] > l2c["o"]
            # Not a hard reject but penalizes in confidence if bearish

            # ── RULE 3A: Leg2 RSI > Leg1 RSI ────────────────────────
            abs2 = len(candles) - 120 + leg2_idx
            if abs2 < 14: continue
            rsi2 = calc_rsi(closes[:abs2+1])
            if rsi2 is None: continue
            if rsi2 <= rsi1: continue           # Must be higher (divergence)

            # ── RULE 3B: Leg2 RSI must be ABOVE 30 ──────────────────
            if rsi2 <= 30: continue             # Must close out of oversold

            # ── RULE 3C: RSI gap must be meaningful (min 5 pts) ──────
            rsi_gap = rsi2 - rsi1
            if rsi_gap < 5: continue            # Weak divergence = skip

            # ── RULE 3D: Current candle must confirm continuation ─────
            # Last 3 candles should not be making new lows (momentum shifted)
            last3_lows = [recent[k]["l"] for k in range(leg2_idx, min(leg2_idx+3, len(recent)))]
            if len(last3_lows) >= 2:
                if last3_lows[-1] < l2c["l"]: continue  # Still making lower lows = not done

            # ── ALL RULES PASSED ─────────────────────────────────────
            current_rsi = calc_rsi(closes)
            cur_price   = closes[-1]

            # Entry = close of Leg2 candle (confirmed close above Leg1)
            entry = l2c["c"]
            sl    = l2c["l"] * 0.998  # Just below the sweep wick
            risk  = entry - sl
            if risk <= 0: continue

            setup = {
                "confirmed":      True,
                "tf":             tf,
                "leg1_low":       leg1_low,
                "leg1_rsi":       rsi1,
                "leg2_low":       l2c["l"],
                "leg2_close":     l2c["c"],
                "leg2_rsi":       rsi2,
                "rsi_gap":        round(rsi_gap, 1),
                "sweep_pct":      round(sweep_pct, 3),
                "bounce_pct":     round(bounce_pct, 2),
                "is_bullish_candle": is_bullish_candle,
                "vol_ratio":      calc_volume_ratio(recent, leg2_idx),
                "current_rsi":    current_rsi,
                "current_price":  cur_price,
                "entry":          entry,
                "sl":             sl,
                "tp1":            entry + risk * 1.5,
                "tp2":            entry + risk * 2.5,
                "tp3":            entry + risk * 4.0,
            }

            conf, label, emoji, reasons = calc_confidence(setup)
            setup["confidence"]   = conf
            setup["conf_label"]   = label
            setup["conf_emoji"]   = emoji
            setup["conf_reasons"] = reasons
            return setup

    return None

# ── HELPERS ───────────────────────────────────────────────────────────
def fmt(n):
    if n is None: return "N/A"
    if n >= 1000: return f"{n:.2f}"
    if n >= 1:    return f"{n:.4f}"
    if n >= 0.01: return f"{n:.5f}"
    return f"{n:.8f}"

def now():
    return datetime.now().strftime("%H:%M:%S")

def get_category(pair):
    if pair in ["XAUTUSDT","PAXGUSDT"]: return "🥇 Commodity"
    bstocks = ["SPCXBUSDT","TSLABUSDT","NVDABUSDT","INTCBUSDT","MSTRBUSDT",
               "AMDBASDT","CRCLBUSDT","SNDKBUSDT","MUBUSDT","EWYBUSDT",
               "COINBUSDT","ROBHBUSDT","UBERBUSDT","GOOGBUSDT"]
    return "📈 bStock" if pair in bstocks else "🪙 Crypto"

def tp_prob(conf):
    return min(95, 50+conf*0.45), min(85, 30+conf*0.45), min(70, 15+conf*0.45)

# ── SUMMARY MESSAGE ───────────────────────────────────────────────────
def build_summary(top10, scan_count, total_scanned, total_found):
    lines = [
        f"🏆 <b>TOP {len(top10)} CONFIRMED SETUPS</b>",
        f"📊 Scan #{scan_count} | {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"🔍 Scanned: {total_scanned} pairs | Found: {total_found} setups",
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    ]
    for i, (pair, tf, s) in enumerate(top10, 1):
        t1, t2, t3 = tp_prob(s["confidence"])
        medal = ['🥇','🥈','🥉'][i-1] if i <= 3 else f"#{i}"
        tf_l = {"15m":"15m","1h":"1H","4h":"4H","1d":"1D"}.get(tf, tf)
        lines.append(
            f"{medal} <b>{pair.replace('USDT','/USDT')}</b> {get_category(pair)} [{tf_l}]\n"
            f"   {s['conf_emoji']} <b>{s['confidence']}/100</b> — {s['conf_label']}\n"
            f"   📍 <code>{fmt(s['entry'])}</code> | 🛑 <code>{fmt(s['sl'])}</code>\n"
            f"   TP1: <b>{t1:.0f}%</b> | TP2: <b>{t2:.0f}%</b> | TP3: <b>{t3:.0f}%</b>\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📩 Detailed alerts follow ↓")
    return "\n".join(lines)

# ── INDIVIDUAL ALERT ──────────────────────────────────────────────────
def build_alert(rank, pair, tf, s):
    cat = get_category(pair)
    tf_l = {"15m":"15 Min","1h":"1 Hour","4h":"4 Hours","1d":"Daily"}.get(tf, tf)
    t1, t2, t3 = tp_prob(s["confidence"])
    medal = ['🥇','🥈','🥉'][rank-1] if rank <= 3 else f"#{rank}"
    reasons = "\n".join(s["conf_reasons"])

    return (
        f"{medal} <b>RANK #{rank} — {pair.replace('USDT','/USDT')}</b>\n"
        f"{cat} | ⏱ {tf_l}\n\n"
        f"{s['conf_emoji']} <b>Confidence: {s['confidence']}/100 — {s['conf_label']}</b>\n\n"
        f"<b>📊 Setup Confirmed:</b>\n"
        f"〰 Leg 1 Low: <code>{fmt(s['leg1_low'])}</code> RSI: {s['leg1_rsi']} ✅ &lt;30\n"
        f"〰 Leg 2 Wick: <code>{fmt(s['leg2_low'])}</code> swept below ✅\n"
        f"〰 Leg 2 Body: <code>{fmt(s['leg2_close'])}</code> closed above Leg 1 ✅\n"
        f"〰 Leg 2 RSI: {s['leg2_rsi']} &gt; {s['leg1_rsi']} + above 30 ✅\n"
        f"〰 Sweep: {s['sweep_pct']}% | Vol: {s.get('vol_ratio','-')}x\n\n"
        f"<b>🎯 TP Hit Probability:</b>\n"
        f"✅ TP1: <b>{t1:.0f}%</b> chance\n"
        f"🎯 TP2: <b>{t2:.0f}%</b> chance\n"
        f"🏆 TP3: <b>{t3:.0f}%</b> chance\n\n"
        f"<b>💰 Trade Levels:</b>\n"
        f"📍 Entry:     <code>{fmt(s['entry'])}</code>\n"
        f"✅ TP1:       <code>{fmt(s['tp1'])}</code>\n"
        f"🎯 TP2:       <code>{fmt(s['tp2'])}</code>\n"
        f"🏆 TP3:       <code>{fmt(s['tp3'])}</code>\n"
        f"🛑 Stop Loss: <code>{fmt(s['sl'])}</code>\n"
        f"📉 RSI Now:   {s['current_rsi']}\n"
        f"⚖️ R:R:       1:2.5\n\n"
        f"<b>📋 Confidence Breakdown:</b>\n"
        f"{reasons}\n\n"
        f"⚠️ Verify on chart before entering!"
    )

# ── MAIN SCAN ─────────────────────────────────────────────────────────
def scan(pairs, scan_count):
    all_setups = []
    total_scanned = 0
    total_pairs = len(pairs)

    for tf in TIMEFRAMES:
        tf_l = {"15m":"15m","1h":"1H","4h":"4H","1d":"Daily"}.get(tf, tf)
        print(f"\n[{now()}] ── {tf_l}: scanning {total_pairs} pairs...")

        for idx, pair in enumerate(pairs):
            try:
                # Progress every 50 pairs
                if idx % 50 == 0:
                    print(f"[{now()}]    {tf_l} progress: {idx}/{total_pairs}...")

                candles = fetch_candles(pair, tf)
                if not candles:
                    continue

                setup = detect_setup(candles, tf)
                total_scanned += 1

                if setup and setup["confirmed"]:
                    key = f"{pair}-{tf}-{fmt(setup['entry'])}-{fmt(setup['leg2_low'])}"
                    if key not in notified:
                        all_setups.append((pair, tf, setup, key))
                        print(f"[{now()}] ✅ {pair} {tf_l} | "
                              f"Conf:{setup['confidence']}/100 "
                              f"L1RSI:{setup['leg1_rsi']} "
                              f"L2RSI:{setup['leg2_rsi']} "
                              f"Sweep:{setup['sweep_pct']}%")

                time.sleep(0.12)  # rate limit protection
            except Exception as e:
                if "Too Many Requests" in str(e) or "429" in str(e):
                    print(f"[{now()}] ⚠️ Rate limit hit — sleeping 10s")
                    time.sleep(10)
                else:
                    pass  # silent fail for individual pairs

    total_found = len(all_setups)
    print(f"\n[{now()}] Scan complete: {total_scanned} scanned, {total_found} confirmed setups found")

    if total_found == 0:
        print(f"[{now()}] No new confirmed setups — skipping Telegram")
        return 0

    # Sort by confidence — take TOP 10
    all_setups.sort(key=lambda x: x[2]["confidence"], reverse=True)
    top10 = all_setups[:TOP_N]

    # Send summary
    summary = build_summary(
        [(p,t,s) for p,t,s,_ in top10],
        scan_count, total_scanned, total_found
    )
    ok = send_telegram(summary)
    print(f"[{now()}] {'✅' if ok else '❌'} Summary sent")
    time.sleep(2)

    # Send individual alerts
    for rank, (pair, tf, setup, key) in enumerate(top10, 1):
        notified.add(key)
        ok = send_telegram(build_alert(rank, pair, tf, setup))
        print(f"[{now()}] {'✅' if ok else '❌'} Alert #{rank}: {pair} {tf} Conf:{setup['confidence']}/100")
        time.sleep(1)

    return len(top10)

# ── ENTRY POINT ───────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  SMC SCANNER PRO — ALL 500+ Markets | Top 10 Only")
    print("=" * 65)

    pairs, pair_data = fetch_all_pairs()

    send_telegram(
        f"🤖 <b>SMC Scanner Pro — ALL Markets</b>\n\n"
        f"✅ Running 24/7 on server\n"
        f"🔍 Scanning: <b>{len(pairs)} pairs</b>\n"
        f"🪙 Crypto + 📈 bStocks + 🥇 Commodities\n"
        f"⏰ Every 5 min | ⏱ 15m · 1H · 4H · Daily\n\n"
        f"<b>Setup:</b> Bullish RSI Div + Liquidity Sweep\n"
        f"<b>Rules:</b>\n"
        f"1️⃣ Leg 1 RSI &lt; 30\n"
        f"2️⃣ Leg 2 wick below Leg 1, body closes above\n"
        f"3️⃣ Leg 2 RSI &gt; Leg 1 + above 30\n\n"
        f"<b>You receive per scan:</b>\n"
        f"📋 1 Summary of TOP 10 ranked setups\n"
        f"📩 10 Detailed alerts with confidence + TP %\n\n"
        f"🔥 Scanning now..."
    )

    scan_count = 0
    while True:
        try:
            scan_count += 1
            print(f"\n{'='*65}")
            print(f"  SCAN #{scan_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Pairs: {len(pairs)}")
            print(f"{'='*65}")

            sent = scan(pairs, scan_count)
            print(f"\n[{now()}] ✅ Scan #{scan_count} done — {sent} alerts sent")
            print(f"[{now()}] Sleeping {SCAN_INTERVAL}s...")

            # Refresh pair list every 10 scans (~50 min)
            if scan_count % 10 == 0:
                pairs, pair_data = fetch_all_pairs()
                notified.clear()
                print(f"[{now()}] 🔄 Pairs refreshed, notifications reset")

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            print("\nScanner stopped.")
            break
        except Exception as e:
            print(f"[{now()}] ❌ Critical error: {e} — retrying in 60s")
            time.sleep(60)

if __name__ == "__main__":
    main()
