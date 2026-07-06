# SMC Scanner Server — Setup Guide

## Deploy on Render.com (FREE — runs 24/7)

### Step 1: Upload to GitHub
1. Go to github.com → Sign up (free)
2. Click "New Repository"
3. Name it: smc-scanner
4. Upload these 3 files:
   - scanner.py
   - requirements.txt
   - render.yaml

### Step 2: Deploy on Render
1. Go to render.com → Sign up (free)
2. Click "New" → "Background Worker"
3. Connect your GitHub account
4. Select your smc-scanner repository
5. Click "Deploy"

### Step 3: Done!
- Scanner runs 24/7 automatically
- You get Telegram alerts instantly when setups confirm
- Render free plan = always on, no cost

## What it does
- Fetches top 100 Binance pairs by volume
- Scans every 5 minutes across 15m, 1h, 4h timeframes
- Detects: Liquidity Sweep, RSI Divergence, Order Blocks, FVG, BOS, CHoCH
- Only sends Telegram alert when ALL conditions confirmed:
  ✅ Score 70+
  ✅ Liquidity sweep confirmed
  ✅ RSI divergence confirmed
  ✅ Last candle green (reversing)
  ✅ RSI < 35 (oversold)
