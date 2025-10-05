import secrets
import string

def generate_access_token(length=32):
    # Define the character set for the token
    alphabet = string.ascii_letters + string.digits
    # Generate a secure random token
    token = ''.join(secrets.choice(alphabet) for _ in range(length))
    return token

if __name__ == "__main__":
    access_token = generate_access_token()
    print(f"Generated Access Token: {access_token}")
    
    # Save the token to .env file
    with open('.env', 'w') as env_file:
        env_file.write(f"ACCESS_TOKEN={access_token}\n")