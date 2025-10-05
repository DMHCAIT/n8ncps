from flask import Flask, render_template, jsonify
import secrets
import string

app = Flask(__name__)

def generate_access_token(length=32):
    alphabet = string.ascii_letters + string.digits
    token = ''.join(secrets.choice(alphabet) for _ in range(length))
    return token

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/generate-token')
def get_token():
    token = generate_access_token()
    # Save token to .env file
    with open('.env', 'w') as env_file:
        env_file.write(f"ACCESS_TOKEN={token}\n")
    return jsonify({"token": token})

if __name__ == '__main__':
    app.run(debug=True)