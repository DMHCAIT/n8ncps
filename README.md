# Streamlit ETF Gap-Down Trader

A Streamlit-based automated trading application that monitors ETFs and executes gap-down trading strategies using the Zerodha Kite API.

## ⚠️ IMPORTANT WARNINGS

- **This is a PROTOTYPE** - Test thoroughly in DRY_RUN mode and Kite's test environment before using real money
- **Use at your own risk** - The authors are not responsible for any financial losses
- **Always start with DRY_RUN=True** to understand the system behavior
- **Verify all orders manually** before switching to live trading

## Features

- **Automated ETF Monitoring**: Continuously polls LTP (Last Traded Price) for a configurable watchlist
- **Gap-Down Detection**: Automatically buys when price drops 2% or more from previous close
- **Target & Stop Management**: Sets 3% profit target and -5% loss alert
- **DRY_RUN Mode**: Test the system without placing real orders
- **Real-time Dashboard**: Monitor positions, P&L, and trading activity
- **Notifications**: Telegram alerts for trades and important events
- **SQLite Persistence**: All trades and positions stored locally
- **Manual Override**: Place manual buy/sell orders through the UI

## Installation

### Local Installation

1. **Clone or download the files**
2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

4. **Run the application**:
   ```bash
   streamlit run streamlit_kite_etf_trader.py
   ```

### Docker Installation

1. **Build the image**:
   ```bash
   docker build -t etf-trader .
   ```

2. **Run with environment file**:
   ```bash
   docker run -p 8501:8501 --env-file .env -v $(pwd)/data:/app/data etf-trader
   ```

## Configuration

### Required Environment Variables

#### Zerodha Kite API
- `KITE_API_KEY`: Your Kite Connect API key
- `KITE_API_SECRET`: Your Kite Connect API secret  
- `KITE_ACCESS_TOKEN`: Your Kite Connect access token (required for live trading)

#### Optional Configuration
- `TELEGRAM_BOT_TOKEN`: Telegram bot token for notifications
- `TELEGRAM_CHAT_ID`: Telegram chat ID for notifications
- `WATCHLIST`: Comma-separated ETF symbols (default: NIFTYBEES,ICICINIFTY,LIQUIDBEES)
- `QTY_PER_TRADE`: Quantity per trade (default: 10)
- `BUY_GAP_PERCENT`: Gap down percentage to trigger buy (default: 2.0)
- `SELL_TARGET_PERCENT`: Profit target percentage (default: 3.0)
- `LOSS_ALERT_PERCENT`: Loss alert percentage (default: 5.0)
- `POLL_INTERVAL_SECONDS`: Seconds between price checks (default: 5)
- `DB_FILE`: SQLite database file path (default: trades.db)

### Getting Zerodha Kite Credentials

1. **Create Kite Connect App**:
   - Login to [Kite Connect](https://kite.zerodha.com)
   - Create a new app to get API_KEY and API_SECRET

2. **Generate Access Token**:
   - For testing: Use Kite's test environment
   - For live trading: Complete the OAuth flow to get ACCESS_TOKEN
   - See [Kite Connect Documentation](https://kite.trade/docs/connect/v3/) for details

## Usage

### Starting the Application

1. **Launch Streamlit**:
   ```bash
   streamlit run streamlit_kite_etf_trader.py
   ```

2. **Access the dashboard**: Open http://localhost:8501 in your browser

### Dashboard Sections

#### Settings Sidebar
- **DRY_RUN Toggle**: Enable/disable paper trading mode
- **Quantity Configuration**: Set shares per trade
- **Watchlist Management**: Add/remove ETF symbols
- **System Status**: Check Kite API connection

#### Main Dashboard
- **Watchlist Table**: Live prices, gaps, positions, and P&L
- **Recent Activity**: Trade history and order log
- **Manual Actions**: Place manual buy/sell orders

### Trading Logic

1. **Monitoring**: System continuously polls ETF prices every 5 seconds
2. **Buy Trigger**: When LTP drops ≥2% from previous close, places market buy order
3. **Position Management**: 
   - Sets 3% profit target (limit sell order)
   - Monitors for 5% loss (sends alert, no auto-sell)
4. **One Trade Per Day**: Prevents multiple buys of the same symbol

## Database Schema

The application uses SQLite with two main tables:

### trades
- Trade history with timestamps, prices, order IDs
- Distinguishes between DRY_RUN and live trades

### positions  
- Current positions with buy prices and targets
- Status tracking (WATCHING, BOUGHT, TARGET_HIT, ALERTED)

## Notifications

Configure Telegram for trade alerts:

1. **Create Telegram Bot**:
   ```
   - Message @BotFather on Telegram
   - Create new bot and get token
   ```

2. **Get Chat ID**:
   ```
   - Message your bot
   - Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
   - Find your chat ID in the response
   ```

3. **Set Environment Variables**:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

## Testing Strategy

### Phase 1: DRY_RUN Testing
1. Set `DRY_RUN=True` in the sidebar
2. Monitor for several hours/days
3. Verify buy signals trigger correctly
4. Check P&L calculations are accurate

### Phase 2: Kite Test Environment
1. Use Kite's test API credentials
2. Set `DRY_RUN=False`
3. Test with small quantities
4. Verify orders are placed correctly

### Phase 3: Live Trading
1. Use real Kite credentials
2. Start with minimal quantities
3. Monitor closely for several trading sessions
4. Gradually increase position sizes

## Limitations & Considerations

### Technical Limitations
- **Polling-based**: Uses REST API polling instead of WebSocket (may have delays)
- **Single-threaded**: One monitoring thread for all symbols
- **No order validation**: Doesn't verify order fills (uses last price as approximation)

### Market Considerations
- **Gap limits only**: No protection against circuit filters or halt
- **Previous close dependency**: Requires accurate previous close data
- **Liquidity**: May not work well with low-volume ETFs
- **Market hours**: No trading hour restrictions implemented

### Risk Management
- **Single strategy**: Only implements gap-down buying
- **No position sizing**: Fixed quantity regardless of account size
- **No correlation checks**: May buy multiple correlated ETFs

## Troubleshooting

### Common Issues

1. **"Kite client not initialized"**
   - Check API credentials in .env file
   - Verify ACCESS_TOKEN is valid and not expired

2. **"Quote fetch failed"**
   - Verify ETF symbols are correct NSE trading symbols
   - Check market hours (API may not return data outside trading hours)

3. **Database errors**
   - Ensure write permissions to database directory
   - Check disk space availability

4. **No buy signals**
   - Verify gap percentage settings
   - Check if symbols were already bought today
   - Ensure previous close data is available

### Debugging

Enable detailed logging by adding print statements or using Python's logging module. Monitor the terminal output for detailed error messages.

## Security Notes

- **Store credentials securely**: Use .env files, never commit secrets to version control
- **API rate limits**: Kite Connect has rate limits; avoid setting POLL_INTERVAL too low
- **Access token expiry**: Kite access tokens expire daily, implement renewal mechanism for production use

## Legal Disclaimer

This software is provided for educational purposes only. Users are responsible for:
- Complying with all applicable financial regulations
- Understanding the risks of automated trading
- Proper testing before live deployment
- Managing their own risk and position sizing

The authors make no guarantees about profitability or safety of this trading strategy.

## Support

For issues related to:
- **Kite API**: Check [Zerodha Kite Connect Documentation](https://kite.trade/docs/)
- **Streamlit**: Check [Streamlit Documentation](https://docs.streamlit.io/)
- **Application bugs**: Review the code and test in DRY_RUN mode first

## License

This project is provided as-is for educational purposes. Use at your own risk.