# 🚀 LIVE TRADING SETUP GUIDE

## ⚠️ CRITICAL WARNINGS

**READ THIS ENTIRE DOCUMENT BEFORE ENABLING LIVE TRADING**

- This system will place REAL orders with REAL money
- You can lose money - only use funds you can afford to lose  
- Always test thoroughly in DRY_RUN mode first
- Start with small quantities and gradually increase
- Monitor the system closely, especially during the first few days

## 📋 Pre-Live Trading Checklist

### 1. ✅ Complete Testing in DRY_RUN Mode
- [ ] Run the system for at least 2-3 trading days in DRY_RUN mode
- [ ] Verify buy signals trigger correctly for your ETFs
- [ ] Check that notifications are working (Telegram/console)
- [ ] Confirm watchlist contains only valid NSE ETF symbols
- [ ] Test manual buy/sell functions in DRY_RUN mode

### 2. ✅ Kite Connect API Setup
- [ ] Create Kite Connect app at https://kite.zerodha.com/connect/login
- [ ] Note down your API_KEY and API_SECRET
- [ ] Generate ACCESS_TOKEN (valid for 1 trading day)
- [ ] Test API connection with small amounts first

### 3. ✅ Risk Management Configuration
- [ ] Set appropriate QTY_PER_TRADE (start small - 1-5 shares)
- [ ] Ensure sufficient account balance (at least 2x your max daily exposure)
- [ ] Configure realistic gap percentage (2% default is aggressive for some ETFs)
- [ ] Set up Telegram notifications for immediate alerts

### 4. ✅ Account Verification
- [ ] Verify you have CNC (Cash and Carry) enabled for ETF trading
- [ ] Ensure your Zerodha account has sufficient margin
- [ ] Check that your chosen ETFs are available for trading
- [ ] Verify market hours and holidays

## 🔧 Live Trading Configuration

### Step 1: Update Environment Variables

Edit your `.env` file:

```bash
# Set these with your actual Kite Connect credentials
KITE_API_KEY=your_actual_api_key
KITE_API_SECRET=your_actual_secret  
KITE_ACCESS_TOKEN=your_daily_access_token

# CRITICAL: Change this to enable live trading
DRY_RUN=false

# Recommended: Set up Telegram for alerts
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Start conservative
QTY_PER_TRADE=1
WATCHLIST=NIFTYBEES,ICICINIFTY
```

### Step 2: Restart the Application

```bash
# Stop current instance (Ctrl+C)
# Then restart
streamlit run streamlit_kite_etf_trader.py
```

### Step 3: Enable Live Trading in UI

1. Open the Streamlit dashboard
2. In the sidebar, **UNCHECK** "DRY_RUN (no real orders)"  
3. **CHECK** "I understand the risks of live trading"
4. The system will now place real orders

## 📊 Monitoring Your Live Trading

### Essential Monitoring
- **Check every 30 minutes** during market hours for the first week
- **Monitor Telegram alerts** - set up phone notifications
- **Watch the dashboard** - keep it open in a browser tab
- **Check your Zerodha positions** independently

### Key Metrics to Watch
- **Available Cash**: Ensure sufficient funds remain
- **Position Count**: Don't over-diversify with limited capital
- **P&L**: Track daily and overall performance  
- **Gap Triggers**: Verify buy signals are reasonable

### Daily Routine
1. **Morning**: Check ACCESS_TOKEN validity, restart if needed
2. **During Market**: Monitor positions and alerts
3. **Evening**: Review day's trades and P&L

## 🚨 Emergency Procedures

### If Something Goes Wrong
1. **Immediate**: Click "Emergency Stop" in sidebar (stops new orders)
2. **Manual**: Go to Zerodha Kite web/app and manually close positions
3. **System**: Stop the Streamlit application completely

### Common Issues & Solutions

**"Kite API connection failed"**
- ACCESS_TOKEN expired (regenerate daily)
- API_KEY/SECRET incorrect
- Zerodha account restrictions

**"Insufficient funds" error**  
- Add more cash to your account
- Reduce QTY_PER_TRADE
- Close existing positions

**"Order placement failed"**
- ETF may be halted or circuit limited
- Check if symbol is correct NSE trading symbol
- Verify account permissions

**Too many buy signals**
- Increase BUY_GAP_PERCENT (make it more selective)  
- Reduce watchlist size
- Add more filters to strategy

## 💡 Best Practices

### Risk Management
- **Start Small**: Use 1-2 shares per trade initially
- **Limited Watchlist**: Start with 2-3 reliable ETFs
- **Daily Limits**: Set informal daily loss limits
- **Regular Reviews**: Weekly analysis of performance

### Position Management  
- **Don't Hold Overnight**: This is a day-trading strategy
- **Take Profits**: Don't get greedy, stick to 3% target
- **Cut Losses**: Manually exit positions at 5% loss
- **Avoid FOMO**: Don't chase every gap-down

### System Maintenance
- **Daily ACCESS_TOKEN**: Regenerate every morning
- **Monitor Logs**: Check console output for errors
- **Backup Database**: Regular backup of trades.db
- **Update Strategy**: Adjust parameters based on performance

## 📈 Gradual Scaling Strategy

### Week 1: Minimal Risk
- QTY_PER_TRADE = 1
- WATCHLIST = 1-2 most liquid ETFs
- Monitor every 15 minutes

### Week 2-3: Cautious Expansion  
- QTY_PER_TRADE = 2-5
- WATCHLIST = 3-4 ETFs
- Monitor every 30 minutes

### Week 4+: Full Operation
- QTY_PER_TRADE = Your target quantity
- WATCHLIST = Full list of desired ETFs
- Monitor hourly (if performance is stable)

## 🔄 Access Token Management

Kite ACCESS_TOKEN expires daily. You need to:

1. **Manual Method**: Login to Kite Connect daily and generate new token
2. **Automated Method**: Implement OAuth flow (advanced)

For daily manual renewal:
```bash
# Update .env file daily with new ACCESS_TOKEN
KITE_ACCESS_TOKEN=new_token_here
# Restart Streamlit application
```

## 📞 Support & Resources

- **Zerodha Support**: https://support.zerodha.com
- **Kite Connect Docs**: https://kite.trade/docs/connect/v3/
- **NSE ETF List**: https://www.nseindia.com/products-services/indices-etf
- **Emergency**: Always have Zerodha Kite app on your phone

## ⚖️ Legal & Compliance

- Ensure you comply with local trading regulations
- Keep records of all trades for tax purposes  
- Understand the risks of algorithmic trading
- Consider consulting a financial advisor

---

**Remember: This is an educational tool. Start small, stay vigilant, and never risk more than you can afford to lose.**