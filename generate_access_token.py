from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv
import webbrowser

def generate_access_token():
    # Load environment variables
    load_dotenv()
    
    # Get API credentials from environment variables
    api_key = os.getenv('KITE_API_KEY')
    api_secret = os.getenv('KITE_API_SECRET')
    
    # Initialize Kite
    kite = KiteConnect(api_key=api_key)
    
    # Get the login URL
    login_url = kite.login_url()
    print("\n1. Opening browser for Kite login...")
    webbrowser.open(login_url)
    
    # Get request token from user
    request_token = input("\n2. After login, paste the request token from the redirect URL here: ")
    
    try:
        # Generate session
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        
        # Update .env file with new access token
        with open('.env', 'r') as file:
            lines = file.readlines()
        
        with open('.env', 'w') as file:
            for line in lines:
                if line.startswith('KITE_ACCESS_TOKEN='):
                    file.write(f'KITE_ACCESS_TOKEN={access_token}\n')
                else:
                    file.write(line)
        
        print("\n✅ Success! Access token has been updated in .env file")
        print(f"Access Token: {access_token}")
        
    except Exception as e:
        print(f"\n❌ Error generating access token: {str(e)}")

if __name__ == "__main__":
    generate_access_token()