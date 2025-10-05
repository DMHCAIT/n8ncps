from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv

def generate_access_token(request_token):
    # Load environment variables
    load_dotenv()
    
    # Get API credentials
    api_key = os.getenv('KITE_API_KEY')
    api_secret = os.getenv('KITE_API_SECRET')
    
    # Initialize Kite
    kite = KiteConnect(api_key=api_key)
    
    try:
        # Generate session
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        
        # Update .env file
        with open('.env', 'r') as file:
            lines = file.readlines()
        
        with open('.env', 'w') as file:
            for line in lines:
                if line.startswith('KITE_ACCESS_TOKEN='):
                    file.write(f'KITE_ACCESS_TOKEN={access_token}\n')
                else:
                    file.write(line)
        
        print("\n✅ Success! Access token has been generated and saved")
        print(f"Access Token: {access_token}")
        return access_token
        
    except Exception as e:
        print(f"\n❌ Error generating access token: {str(e)}")
        return None

# Use the provided request token
request_token = "UCSFCPjcdEQilea1duZwCITfYFVc2NnV"
generate_access_token(request_token)