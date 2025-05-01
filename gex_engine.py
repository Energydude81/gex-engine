#!/usr/bin/env python3
"""
gex_engine.py

Automatisierte GEX‐Berechnung für 0DTE SPX‐Optionen via
TradeStation Client Credentials Grant + Versand eines Webhooks an OptionAlpha.

Dieser Script liest alle sensiblen Daten aus Umgebungsvariablen
und kann deshalb sicher in GitHub Actions laufen.
"""

import os
import sys
import datetime
import requests
import pandas as pd

# === 1) Credentials aus Umgebungsvariablen ==========================
CLIENT_ID     = os.getenv('TS_CLIENT_ID')
CLIENT_SECRET = os.getenv('TS_CLIENT_SECRET')
OA_WEBHOOK    = os.getenv('OA_WEBHOOK')

if not all([CLIENT_ID, CLIENT_SECRET, OA_WEBHOOK]):
    sys.exit("Error: Missing env vars TS_CLIENT_ID, TS_CLIENT_SECRET or OA_WEBHOOK")

# === 2) Token via Client Credentials Grant holen ===================
token_url = 'https://signin.tradestation.com/oauth/token'
token_data = {
    'grant_type':    'client_credentials',
    'client_id':     CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'audience':      'https://api.tradestation.com'
}

r = requests.post(token_url, data=token_data)
if r.status_code != 200:
    sys.exit(f"Token-Request failed [{r.status_code}]: {r.text}")

token = r.json().get('access_token')
if not token:
    sys.exit("Error: no access_token in response")
print("✅ Access Token erhalten.")

# === 3) Options-Chain + Greeks laden ===============================
headers = {'Authorization': f'Bearer {token}'}

# Wir wollen nur 0DTE, also heute als Ablaufdatum
today = datetime.date.today().isoformat()
url = f"https://api.tradestation.com/v2/marketdata/etfs/optionschains/SPX?expirationdate={today}"

resp = requests.get(url, headers=headers)
if resp.status_code != 200:
    sys.exit(f"Options-Chain request failed [{resp.status_code}]: {resp.text}")

data = resp.json()
chain_raw = data['optionChains'][0]['options']
chain = pd.DataFrame(chain_raw)

underlying_price = data.get('underlyingPrice')
if underlying_price is None:
    sys.exit("Error: underlyingPrice not found in response")
print(f"Letzter SPX-Preis: {underlying_price}")

# === 4) GEX, Call-/Put-Wall und Zero-Gamma-Level berechnen ==========
# Gamma-Exposure in Shares (Gamma * OI * 100)
chain['gex_shares'] = chain['greeks']['gamma'] * chain['openInterest'] * 100

# Call- und Put-Wall (Strike mit größter absol. Exposure)
calls = chain[chain['optionType'] == 'call']
puts  = chain[chain['optionType'] == 'put']
call_wall = float(calls.groupby('strikePrice')['gex_shares'].sum().abs().idxmax())
put_wall  = float(puts.groupby('strikePrice')['gex_shares'].sum().abs().idxmax())

# Netto-GEX ($ pro 1% Move) = Sum(gex_shares) * underlying_price / 100
total_gex = float(chain['gex_shares'].sum() * underlying_price / 100.0)

# Zero-Gamma-Level: erster Strike, bei dem kumul. GEX ≥ 0
cum = (
    chain
    .groupby('strikePrice')['gex_shares']
    .sum()
    .sort_index()
    .cumsum()
)
zero_gamma = float(cum[cum >= 0].index.min())

print(f"Total-GEX: {total_gex:.0f} $/1%")
print(f"Call-Wall: {call_wall}")
print(f"Put-Wall:  {put_wall}")
print(f"Zero-Gamma-Level: {zero_gamma}")

# === 5) Payload an Webhook senden =====================================
sig = 'FLAT'
if total_gex > 0:
    sig = 'LONG'
elif total_gex < 0:
    sig = 'SHORT'

payload = {
    'sig': sig,
    'gex': total_gex,
    'cw':  call_wall,
    'pw':  put_wall,
    'zg':  zero_gamma
}

response = requests.post(OA_WEBHOOK, json=payload)
if not (200 <= response.status_code < 300):
    sys.exit(f"Webhook error [{response.status_code}]: {response.text}")

print(f"Webhook-Payload: {payload}")
