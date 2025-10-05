# 🚀 Deployment Guide - ETF Trading Bot

## Option 1: Streamlit Cloud (Recommended - Free)

### Steps:
1. **Deploy to Streamlit Cloud:**
   - Go to [share.streamlit.io](https://share.streamlit.io)
   - Connect your GitHub account
   - Select repository: `DMHCAIT/n8ncps`
   - Main file path: `streamlit_kite_etf_trader.py`
   - Click "Deploy"

2. **Configure Secrets:**
   - In your Streamlit Cloud app dashboard, click "Advanced settings"
   - Go to "Secrets" tab
   - Copy content from `secrets_template.toml` and paste
   - Update with your actual values:
     ```toml
     KITE_API_KEY = "your_actual_api_key"
     KITE_API_SECRET = "your_actual_secret"
     KITE_ACCESS_TOKEN = "generate_from_app"
     ```

3. **Your app will be available at:**
   `https://your-app-name.streamlit.app`

## Option 2: Railway.app

1. Go to [railway.app](https://railway.app)
2. Connect GitHub repository
3. Add environment variables
4. Deploy

## Option 3: Heroku

1. Create Heroku app
2. Connect GitHub repository
3. Add environment variables in Config Vars
4. Deploy

## Option 4: DigitalOcean App Platform

1. Go to DigitalOcean Apps
2. Connect GitHub repository
3. Configure environment variables
4. Deploy

## Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
streamlit run streamlit_kite_etf_trader.py
```

## Important Notes:

1. **Never commit real API keys to GitHub**
2. **Use environment variables for all secrets**
3. **Test in DRY_RUN mode first**
4. **Generate access token through the app UI**

## Troubleshooting:

- If modules are missing, check `requirements.txt`
- If secrets are not loading, check Streamlit Cloud secrets configuration
- For API errors, verify Zerodha credentials