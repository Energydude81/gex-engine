#!/usr/bin/env python3
"""
gex_engine.py

Automatisierte GEX-Berechnung für 0DTE SPX-Optionen via
TradeStation Password Grant + Versand eines Webhooks an OptionAlpha.

Dieser Script liest alle sensiblen Daten aus Umgebungs­variablen
und kann deshalb sicher in GitHub Actions laufen.
"""

import os
import sys
import datetime
import requests
import pandas as pd

# === 1) Credentials aus Umgebungsvariablen ==============================
CLIENT_ID     = os.getenv('TS_CLIENT_ID')
CLIENT_SECRET = os.getenv('TS_CLIENT_SECRET')
USERNAME      = os.getenv('TS_USERNAME')
PASSWORD      = os.getenv('TS_PASSWORD')
OA_WEBHOOK    = os.getenv('OA_WEBHOOK')

if not all([CLIENT_ID, CLIENT_SECRET, USERNAME, PASSWORD, OA_WEBHOOK]):
    sys.exit("Missing env vars: TS_CLIENT_ID, TS_CLIENT_SECRET, "
             "TS_USERNAME, TS_PASSWORD, OA_WEBHOOK")

# === 2) Token via Resource Owner Password Grant holen ===================
token_url = 'https://signin.tradestation.com/oauth/token'
token_data = {
    'grant_type':    'password',
    'client_id':     CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'username':      USERNAME,
    'password':      PASSWORD,
    'scope':         'MarketData openid profile offline_access'
}
r = requests.post(token_url, data=token_data)
if r.status_code != 200:
    sys.exit(f"Token-Request failed [{r.status_code}]: {r.text}")
token = r.json().get('access_token')
if not token:
    sys.exit("Error: no access_token in response")
print("✅ Access Token erhalten.")

# === 3) Options-Chain + Greeks laden ====================================
headers = {'Authorization': f'Bearer {token}'}
expiry = datetime.date.today().isoformat()  # z.B. "2025-05-01"
chain_url = (
    "https://api.tradestation.com/v3/marketdata/options/chains"
    f"?symbolRoot=SPX&expirationDate={expiry}&includeGreeks=true"
)
r = requests.get(chain_url, headers=headers)
if r.status_code != 200:
    sys.exit(f"Chain-Request failed [{r.status_code}]: {r.text}")
data = r.json()

# === 4) GEX-Berechnung ===================================================
rows = []
for pair in data.get('optionPairs', []):
    for leg in pair.get('legs', []):
        greek = leg.get('greek') or {}
        oi    = leg.get('openInterest', 0)
        if 'gamma' in greek and oi > 0:
            rows.append({
                'strike': leg['strikePrice'],
                'gamma':  greek['gamma'],
                'oi':     oi
            })

if not rows:
    sys.exit("Error: keine Optionsdaten erhalten")

df = pd.DataFrame(rows)
df['gexShares'] = df['gamma'] * df['oi'] * 100

# Unterliegenden Spot-Preis holen
r2 = requests.get("https://api.tradestation.com/v3/marketdata/quotes/SPX", headers=headers)
if r2.status_code != 200:
    sys.exit(f"Spot-Request failed [{r2.status_code}]: {r2.text}")
spot = r2.json().get('Last')
if not spot:
    sys.exit("Error: kein SPX-Spot erhalten")

# Total-GEX ($ pro 1% Move)
total_gex = df['gexShares'].sum() * spot * 0.01

# Call-Wall & Put-Wall bestimmen
wall      = df.groupby('strike')['gexShares'].sum()
call_wall = int(wall.idxmax())
put_wall  = int(wall.idxmin())

# Zero-Gamma-Level ermitteln
zero_gamma = int(wall.sort_index().cumsum().abs().idxmin())

print(f"\nTotal-GEX  : {total_gex/1e9:.2f} Mrd $ / 1 %")
print(f"Call-Wall  : {call_wall}")
print(f"Put-Wall   : {put_wall}")
print(f"Zero-Gamma : {zero_gamma}")

# === 5) Payload bauen & an OptionAlpha senden ============================
payload = {
    'sig': 'CONDOR' if total_gex > 2e9 else 'FLAT',
    'gex': total_gex,
    'cw':  call_wall,
    'pw':  put_wall,
    'zg':  zero_gamma
}
print("\nWebhook-Payload:", payload)

wh = requests.post(OA_WEBHOOK, json=payload)
if wh.status_code != 200:
    print("⚠️ Webhook-Fehler:", wh.status_code, wh.text)
else:
    print("✅ Webhook erfolgreich gesendet.")
