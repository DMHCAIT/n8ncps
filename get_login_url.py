from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get API key from environment
api_key = os.getenv('KITE_API_KEY')

# Initialize Kite
kite = KiteConnect(api_key=api_key)

# Get the login URL
login_url = kite.login_url()
print("\nKite Login URL:")
print("==============")
print(login_url)
print("\nInstructions:")
print("1. Open this URL in your browser")
print("2. Login with your Zerodha credentials")
print("3. After login, you'll be redirected to a URL")
print("4. Copy the 'request_token' parameter from that URL")