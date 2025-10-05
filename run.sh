#!/bin/bash

echo "🚀 TURTEL ETF Trader Launcher"
echo "=============================="

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "1️⃣  Generating Access Token..."
python generate_access_token.py

echo -e "\n2️⃣  Starting Streamlit Application..."
echo "----------------------------------------"
streamlit run streamlit_kite_etf_trader.py