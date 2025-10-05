#!/usr/bin/env python3
"""
Kite Connect Access Token Generator

This script helps you generate the access token needed for live trading.
Run this script and follow the OAuth flow to get your access token.
"""

import os
import webbrowser
from kiteconnect import KiteConnect
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if not API_KEY or not API_SECRET:
    print("❌ Error: KITE_API_KEY and KITE_API_SECRET must be set in .env file")
    exit(1)

# Initialize KiteConnect
kite = KiteConnect(api_key=API_KEY)

# Step 1: Get the login URL
login_url = kite.login_url()
print("🔐 Kite Connect OAuth Flow")
print("=" * 50)
print(f"📍 Opening login URL: {login_url}")
print()
print("Steps to follow:")
print("1. A browser window will open with Kite login page")
print("2. Login with your Zerodha credentials")
print("3. After successful login, you'll be redirected to a URL")
print("4. Copy the 'request_token' from the redirected URL")
print("5. Paste it below when prompted")
print()

# Open the login URL in browser
try:
    webbrowser.open(login_url)
    print("✅ Browser opened. Please complete the login process.")
except Exception as e:
    print(f"⚠️  Could not open browser automatically: {e}")
    print(f"Please manually open this URL: {login_url}")

print()
print("After login, the URL will look like:")
print("http://127.0.0.1:5001/?request_token=XXXXXXXXXXXXX&action=login&status=success")
print()

# Get request token from user
request_token = input("📝 Enter the request_token from the URL: ").strip()

if not request_token:
    print("❌ Error: Request token is required")
    exit(1)

try:
    # Step 2: Generate access token
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    
    print()
    print("🎉 SUCCESS! Access token generated:")
    print("=" * 50)
    print(f"ACCESS_TOKEN: {access_token}")
    print()
    print("📝 To use this token:")
    print("1. Copy the access token above")
    print("2. Update your .env file:")
    print(f"   KITE_ACCESS_TOKEN={access_token}")
    print()
    print("⚠️  IMPORTANT NOTES:")
    print("- Access tokens expire daily at 7:30 AM IST")
    print("- You'll need to regenerate the token each day for live trading")
    print("- Keep your access token secure and never share it")
    print()
    
    # Ask if user wants to update .env automatically
    update_env = input("🤔 Do you want to automatically update the .env file? (y/n): ").lower()
    
    if update_env in ['y', 'yes']:
        # Read current .env content
        with open('.env', 'r') as f:
            lines = f.readlines()
        
        # Update the access token line
        updated = False
        for i, line in enumerate(lines):
            if line.startswith('KITE_ACCESS_TOKEN='):
                lines[i] = f'KITE_ACCESS_TOKEN={access_token}\n'
                updated = True
                break
        
        # Write back to .env
        if updated:
            with open('.env', 'w') as f:
                f.writelines(lines)
            print("✅ .env file updated successfully!")
        else:
            print("⚠️  Could not find KITE_ACCESS_TOKEN line in .env file")
            print(f"   Please manually add: KITE_ACCESS_TOKEN={access_token}")
    
    print()
    print("🚀 Your ETF trader is now ready for LIVE trading!")
    print("   Start the app with: streamlit run streamlit_kite_etf_trader.py")
    
except Exception as e:
    print(f"❌ Error generating access token: {e}")
    print()
    print("💡 Common issues:")
    print("- Make sure the request_token is correct")
    print("- Ensure API_KEY and API_SECRET are valid")
    print("- Check if your Kite Connect app is active")