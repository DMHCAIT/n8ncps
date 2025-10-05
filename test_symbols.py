#!/usr/bin/env python3
"""Test all symbols in watchlist to find valid ones"""

import os
import time
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# Load environment variables
load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

# Initialize Kite
kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# Test all symbols from the watchlist
watchlist_symbols = [
    "NIFTYBEES", "UTISENSETF", "INDA", "MOM150ETF", "MOM250ETF", "ICICINXT50", 
    "SETFNIF100", "KOTAKNIFTY200", "ABSLNIFTY500ETF", "MOMMICROETF", "BANKBEES", 
    "ITBEES", "PSUBANKBEES", "PHARMABEES", "FMCGBEES", "ENERGYBEES", "INFRAETF", 
    "AUTOETF", "REALTYETF", "MEDIAETF", "PRBANKETF", "METALETF", "COMMODETF", 
    "SERVICESETF", "CONSUMETF", "DIVOPPBEES", "GROWTHETF", "MNCETF", "GS813ETF", 
    "GS5YEARETF", "CPSEETF", "ICICIB22", "MON100ETF", "MOSP500ETF", "MOEAFEETF", 
    "MOEMETF", "ICICIESGETF", "ALPHALVETF", "QUALITYETF", "VALUEETF", "LOWVOLETF", 
    "EQUALWEIGHTETF", "BHARATBONDETFAPR30", "BHARATBONDETFAPR25", "LIQUIDBEES", 
    "EDEL1DRATEETF", "SBISDL26ETF", "ICICISDL27ETF", "HDFCGSEC30ETF", "SILVERBEES", 
    "GOLDBEES", "ICICIMID50", "ICICISMALL100", "ALPHA50ETF", "EDELMOM30", 
    "ICICIHDIV", "ICICIFINSERV", "ICICIHEALTH", "ICICIDIGITAL", "ICICIMANUF"
]

print("Testing all watchlist symbols for valid prices...")
valid_symbols = []
invalid_symbols = []

# Test in batches to avoid rate limits
for i, symbol in enumerate(watchlist_symbols):
    nse_symbol = f"NSE:{symbol}"
    try:
        quote = kite.quote(nse_symbol)
        if nse_symbol in quote and 'last_price' in quote[nse_symbol]:
            ltp = quote[nse_symbol]['last_price']
            if ltp > 0:  # Valid price
                valid_symbols.append(symbol)
                print(f"✅ {symbol}: ₹{ltp}")
            else:
                invalid_symbols.append(symbol)
                print(f"❌ {symbol}: Zero price")
        else:
            invalid_symbols.append(symbol)
            print(f"❌ {symbol}: No price data")
    except Exception as e:
        invalid_symbols.append(symbol)
        print(f"❌ {symbol}: {str(e)}")
    
    # Small delay to avoid rate limits
    if i % 10 == 9:
        time.sleep(1)

print(f"\n📊 SUMMARY:")
print(f"✅ Valid symbols: {len(valid_symbols)}")
print(f"❌ Invalid symbols: {len(invalid_symbols)}")
print(f"\n🔧 VALID WATCHLIST (copy this to .env):")
print(",".join(valid_symbols))