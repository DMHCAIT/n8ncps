"""
Streamlit + Zerodha Kite ETF Trader (single-file prototype)

Filename: streamlit_kite_etf_trader.py

Purpose:
- Monitor a configurable watchlist of ETFs via Zerodha Kite (LTP polling).
- Buy a fixed quantity when LTP gaps down >= 2% vs previous close.
- After buy, set a limit sell at +3% from buy price.
- Send alert (no auto-sell) when price reaches -5% from buy.
- DRY_RUN default True (no real orders executed).
- Simple Streamlit dashboard for monitoring and manual actions.
- Persistence via SQLite.

NOTES & WARNINGS:
- This is a prototype. Test thoroughly in DRY_RUN / Kite test environment before using real money.
- You must provide Kite API credentials and ACCESS_TOKEN via environment variables.

Required environment variables:
- KITE_API_KEY
- KITE_API_SECRET
- KITE_ACCESS_TOKEN  (optional for DRY_RUN but required for LIVE)
- TELEGRAM_BOT_TOKEN (optional)
- TELEGRAM_CHAT_ID   (optional)
- SMTP_* for email notifications (optional)

How to run:
1. pip install -r requirements.txt
2. streamlit run streamlit_kite_etf_trader.py

Quick example requirements.txt (put alongside or pip install manually):
# requirements.txt
streamlit
kiteconnect
pandas
python-dotenv
requests
schedule

Dockerfile (basic):
FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --upgrade pip && pip install streamlit kiteconnect pandas python-dotenv requests schedule
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_kite_etf_trader.py", "--server.port=8501", "--server.address=0.0.0.0"]

"""

import os
import time
import threading
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
try:
    import plotly.graph_objs as go
except ImportError:
    go = None

# Optional imports; catch-friendly if kiteconnect not installed
try:
    from kiteconnect import KiteConnect
except Exception:
    KiteConnect = None

# ---- Configuration ----
load_dotenv()

KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_FILE = os.getenv("DB_FILE", "trades.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))  # seconds between LTP polls
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# Default watchlist - replace with real NSE tradingsymbols
DEFAULT_WATCHLIST = [
    "NIFTYBEES", "UTISENSETF", "INDA", "MOM150ETF", "MOM250ETF", "ICICINXT50", "SETFNIF100", "KOTAKNIFTY200",
    "ABSLNIFTY500ETF", "MOMMICROETF", "BANKBEES", "ITBEES", "PSUBANKBEES", "PHARMABEES", "FMCGBEES", "ENERGYBEES",
    "INFRAETF", "AUTOETF", "REALTYETF", "MEDIAETF", "PRBANKETF", "METALETF", "COMMODETF", "SERVICESETF", "CONSUMETF",
    "DIVOPPBEES", "GROWTHETF", "MNCETF", "GS813ETF", "GS5YEARETF", "CPSEETF", "ICICIB22", "MON100ETF", "MOSP500ETF",
    "MOEAFEETF", "MOEMETF", "ICICIESGETF", "ALPHALVETF", "QUALITYETF", "VALUEETF", "LOWVOLETF", "EQUALWEIGHTETF",
    "BHARATBONDETFAPR30", "BHARATBONDETFAPR25", "LIQUIDBEES", "EDEL1DRATEETF", "SBISDL26ETF", "ICICISDL27ETF",
    "HDFCGSEC30ETF", "SILVERBEES", "GOLDBEES", "ICICIMID50", "ICICISMALL100", "ALPHA50ETF", "EDELMOM30", "ICICIHDIV",
    "ICICIFINSERV", "ICICIHEALTH", "ICICIDIGITAL", "ICICIMANUF"
]
DEFAULT_QTY = int(os.getenv("QTY_PER_TRADE", "10"))
BUY_GAP_PERCENT = float(os.getenv("BUY_GAP_PERCENT", "2.0"))
SELL_TARGET_PERCENT = float(os.getenv("SELL_TARGET_PERCENT", "3.0"))
LOSS_ALERT_PERCENT = float(os.getenv("LOSS_ALERT_PERCENT", "5.0"))

# DRY_RUN default - set to False for LIVE TRADING
DRY_RUN_DEFAULT = os.getenv("DRY_RUN", "True").lower() == "true"

# ---- Simple helpers ----

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_to_local(dt: datetime) -> datetime:
    # naive conversion: user can adapt to pytz if needed
    return dt.astimezone()


# ---- Persistence (SQLite) ----

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            qty INTEGER,
            side TEXT,
            price REAL,
            timestamp TEXT,
            order_id TEXT,
            dry_run INTEGER,
            extra TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            qty INTEGER,
            avg_buy_price REAL,
            buy_timestamp TEXT,
            target_price REAL,
            status TEXT
        )
        """
    )
    conn.commit()
    return conn

DB = init_db()
DB_LOCK = threading.Lock()


def save_trade(symbol: str, qty: int, side: str, price: float, order_id: Optional[str], dry_run: bool, extra: Optional[Dict] = None):
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute(
            "INSERT INTO trades (symbol, qty, side, price, timestamp, order_id, dry_run, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, qty, side, price, datetime.utcnow().isoformat(), order_id or "", 1 if dry_run else 0, json.dumps(extra or {})),
        )
        DB.commit()


def upsert_position(symbol: str, qty: int, avg_buy_price: float, buy_timestamp: str, target_price: float, status: str):
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT symbol FROM positions WHERE symbol = ?", (symbol,))
        if cur.fetchone():
            cur.execute(
                "UPDATE positions SET qty=?, avg_buy_price=?, buy_timestamp=?, target_price=?, status=? WHERE symbol = ?",
                (qty, avg_buy_price, buy_timestamp, target_price, status, symbol),
            )
        else:
            cur.execute(
                "INSERT INTO positions(symbol, qty, avg_buy_price, buy_timestamp, target_price, status) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, qty, avg_buy_price, buy_timestamp, target_price, status),
            )
        DB.commit()


def load_positions_df() -> pd.DataFrame:
    with DB_LOCK:
        df = pd.read_sql_query("SELECT * FROM positions", DB)
    return df


# ---- Kite wrapper (REST polling) ----

class KiteWrapper:
    def __init__(self, api_key: str, access_token: Optional[str]):
        if KiteConnect is None:
            st.warning("kiteconnect not installed; Kite functionality disabled. Install kiteconnect package to enable live trading.")
            self.kite = None
            return
        
        self.kite = KiteConnect(api_key=api_key)
        if access_token:
            self.kite.set_access_token(access_token)
            # Verify connection by checking profile
            try:
                profile = self.kite.profile()
                print(f"✅ Kite connected successfully for user: {profile.get('user_name', 'Unknown')}")
                print(f"📊 Broker: {profile.get('broker', 'Unknown')}")
            except Exception as e:
                print(f"❌ Kite connection failed: {e}")
                st.error(f"Kite connection failed: {e}")
                self.kite = None

    def quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        # symbol expected like 'NSE:RELIANCE' or tradingsymbol 'RELIANCE'
        if not self.kite:
            return None
            
        try:
            # Ensure symbol has exchange prefix
            if ":" not in symbol:
                full_symbol = f"NSE:{symbol}"
            else:
                full_symbol = symbol
                
            # Call kite.quote with the symbol
            q = self.kite.quote(full_symbol)
            return q
        except Exception as e:
            print(f"❌ Quote fetch failed for {symbol}: {e}")
            return None

    def get_margins(self) -> Dict[str, Any]:
        """Get account margins to verify available funds"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        return self.kite.margins()
    
    def get_order_history(self, order_id: str) -> List[Dict[str, Any]]:
        """Get order execution details"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        return self.kite.order_history(order_id)
    
    def get_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get current positions"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        return self.kite.positions()

    def place_market_buy(self, symbol: str, qty: int) -> Dict[str, Any]:
        """Place market buy order with validation"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        
        # Pre-trade validation
        try:
            # Check margins
            margins = self.get_margins()
            available_cash = margins.get('equity', {}).get('available', {}).get('live_balance', 0)
            
            # Get current quote for rough cost estimation
            quote = self.quote(symbol)
            if quote:
                ltp = quote.get('last_price', 0)
                estimated_cost = ltp * qty * 1.1  # 10% buffer for price movement
                
                if available_cash < estimated_cost:
                    raise RuntimeError(f"Insufficient funds: Available ₹{available_cash:.2f}, Required ~₹{estimated_cost:.2f}")
            
            print(f"💰 Available cash: ₹{available_cash:.2f}")
            print(f"🛒 Placing BUY order: {qty} x {symbol}")
            
        except Exception as e:
            print(f"⚠️ Pre-trade validation warning: {e}")
        
        # Place the order
        return self.kite.place_order(
            tradingsymbol=symbol, 
            exchange="NSE", 
            transaction_type="BUY", 
            quantity=qty, 
            order_type="MARKET", 
            variety="regular", 
            product="CNC"
        )

    def place_limit_sell(self, symbol: str, qty: int, price: float) -> Dict[str, Any]:
        """Place limit sell order"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
            
        print(f"📤 Placing SELL order: {qty} x {symbol} @ ₹{price:.2f}")
        
        return self.kite.place_order(
            tradingsymbol=symbol, 
            exchange="NSE", 
            transaction_type="SELL", 
            quantity=qty, 
            order_type="LIMIT", 
            price=price, 
            variety="regular", 
            product="CNC", 
            validity="DAY"
        )
    
    def place_market_sell(self, symbol: str, qty: int) -> Dict[str, Any]:
        """Place market sell order for emergency exits"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
            
        print(f"🚨 Placing MARKET SELL: {qty} x {symbol}")
        
        return self.kite.place_order(
            tradingsymbol=symbol, 
            exchange="NSE", 
            transaction_type="SELL", 
            quantity=qty, 
            order_type="MARKET", 
            variety="regular", 
            product="CNC"
        )


# Instantiate Kite wrapper (may be None if not configured)
KITE = None
# Capital management functions
def fetch_real_account_balance():
    """Fetch real account balance from Kite"""
    try:
        if not KITE or not KITE.kite:
            print("❌ Kite connection not available")
            return 0.0
            
        margins = KITE.kite.margins()
        equity_margins = margins.get('equity', {})
        available_cash = equity_margins.get('available', {}).get('cash', 0.0)
        
        print(f"💰 Real Account Balance: ₹{available_cash:,.2f}")
        return float(available_cash)
        
    except Exception as e:
        print(f"❌ Error fetching account balance: {e}")
        return 0.0

def update_capital_allocation():
    """Update capital allocation based on real account balance"""
    # Fetch real balance
    total_capital = fetch_real_account_balance()
    
    if total_capital <= 0:
        print("❌ Invalid account balance - cannot proceed with capital allocation")
        return False
    
    # Update MONITOR_STATE with real values
    MONITOR_STATE["total_capital"] = total_capital
    MONITOR_STATE["last_balance_update"] = datetime.now().isoformat()
    
    # Calculate capital buckets
    deployment_capital = total_capital * (MONITOR_STATE["deployment_percentage"] / 100.0)
    reserve_capital = total_capital * (MONITOR_STATE["reserve_percentage"] / 100.0)
    
    print(f"📊 Capital Allocation Updated:")
    print(f"   Total Capital: ₹{total_capital:,.2f}")
    print(f"   Deployment Capital (70%): ₹{deployment_capital:,.2f}")
    print(f"   Reserve Capital (30%): ₹{reserve_capital:,.2f}")
    
    return True

# ---- Notifications ----

import requests


def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
        return resp.ok
    except Exception as e:
        print("Telegram send failed:", e)
        return False


def notify(message: str):
    print("NOTIFY:", message)
    send_telegram(message)
    # TODO: add SMTP/email if needed

# ---- Trading logic ----

MONITOR_STATE = {
    "symbols": DEFAULT_WATCHLIST,
    "qty": 10,  # Default quantity, will be dynamically calculated
    "dry_run": DRY_RUN_DEFAULT,
    "last_prev_close": {},  # symbol -> prev_close
    "bought_today": set(),  # symbols bought today
    
    # Dynamic Capital Allocation Parameters
    "total_capital": 0.0,  # Real account balance
    "deployment_percentage": 70.0,  # 70% for trading
    "reserve_percentage": 30.0,     # 30% buffer
    "per_trade_percentage": 5.0,    # 5% per trade of deployment capital
    "allocated_capital": 0.0,       # Currently used in open trades
    "last_balance_update": None,    # When balance was last fetched
}

# Initialize Kite and capital allocation
if KITE_API_KEY:
    KITE = KiteWrapper(KITE_API_KEY, KITE_ACCESS_TOKEN)
    
    # Initialize capital allocation with real account balance
    if KITE and KITE.kite:
        print("🏦 Initializing capital allocation with real account balance...")
        update_capital_allocation()


def fetch_prev_close(symbol: str) -> Optional[float]:
    # Use kite.quote ohlc to get previous close
    if KITE and KITE.kite:
        try:
            # Add NSE prefix if not present
            nse_symbol = f"NSE:{symbol}" if ":" not in symbol else symbol
            q = KITE.quote(symbol)  # Use KITE.quote (our wrapper method)
            if not q or nse_symbol not in q:
                print(f"❌ No quote data returned for {symbol}")
                return None
            
            data = q[nse_symbol]
            ohlc = data.get("ohlc") or {}
            prev_close = ohlc.get("close")
            
            if prev_close:
                print(f"✅ {symbol}: Previous close ₹{prev_close:.2f}")
            
            return prev_close
        except Exception as e:
            print(f"❌ Error fetching prev close for {symbol}: {e}")
            return None
    else:
        # DRY_RUN/test mode: return a fake previous close if not known
        return None


def fetch_ltp(symbol: str) -> Optional[float]:
    if KITE and KITE.kite:
        try:
            # Add NSE prefix if not present
            nse_symbol = f"NSE:{symbol}" if ":" not in symbol else symbol
            q = KITE.quote(symbol)  # Use KITE.quote (our wrapper method)
            if not q or nse_symbol not in q:
                print(f"❌ No LTP data returned for {symbol}")
                return None
            
            ltp = q[nse_symbol].get("last_price")
            if ltp:
                print(f"✅ {symbol}: LTP ₹{ltp:.2f}")
            
            return ltp
        except Exception as e:
            print(f"❌ Error fetching LTP for {symbol}: {e}")
            return None
    else:
        return None


def fetch_ohlc_history(symbol, interval="5minute", days=5):
    """Fetch OHLC historical data for the symbol from Kite (returns DataFrame)"""
    if not KITE or not KITE.kite:
        return None
    try:
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = datetime.now().strftime("%Y-%m-%d")
        # Kite expects symbol with exchange prefix
        kite_symbol = f"NSE:{symbol}" if ":" not in symbol else symbol
        data = KITE.kite.historical_data(
            KITE.kite.ltp([kite_symbol])[kite_symbol]['instrument_token'],
            from_date,
            to_date,
            interval
        )
        import pandas as pd
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        print(f"❌ Error fetching OHLC for {symbol}: {e}")
        return None


# ---- Dynamic Capital Allocation Functions ----

def fetch_real_account_balance() -> float:
    """Fetch real account balance from Kite API"""
    if not KITE or not KITE.kite:
        print("❌ Kite API not connected - cannot fetch real balance")
        return 0.0
    
    try:
        margins = KITE.kite.margins()
        equity_margins = margins.get('equity', {})
        available_cash = equity_margins.get('available', {}).get('cash', 0.0)
        
        print(f"💰 Real Account Balance: ₹{available_cash:,.2f}")
        return float(available_cash)
        
    except Exception as e:
        print(f"❌ Error fetching account balance: {e}")
        return 0.0


def update_capital_allocation():
    """Update capital allocation based on real account balance"""
    # Fetch real balance
    total_capital = fetch_real_account_balance()
    
    if total_capital <= 0:
        print("❌ Invalid account balance - cannot proceed with capital allocation")
        return False
    
    # Update MONITOR_STATE with real values
    MONITOR_STATE["total_capital"] = total_capital
    MONITOR_STATE["last_balance_update"] = datetime.now().isoformat()
    
    # Calculate capital buckets
    deployment_capital = total_capital * (MONITOR_STATE["deployment_percentage"] / 100.0)
    reserve_capital = total_capital * (MONITOR_STATE["reserve_percentage"] / 100.0)
    
    print(f"📊 Capital Allocation Updated:")
    print(f"   Total Capital: ₹{total_capital:,.2f}")
    print(f"   Deployment Capital (70%): ₹{deployment_capital:,.2f}")
    print(f"   Reserve Capital (30%): ₹{reserve_capital:,.2f}")
    
    return True


def calculate_allocated_capital() -> float:
    """Calculate currently allocated capital from open positions"""
    if not DB:
        return 0.0
        
    try:
        with DB_LOCK:
            cur = DB.cursor()
            cur.execute("""
                SELECT SUM(qty * avg_buy_price) as total_allocated 
                FROM positions 
                WHERE status NOT IN ('TARGET_HIT', 'SOLD')
            """)
            result = cur.fetchone()
            allocated = result[0] if result[0] else 0.0
            
        MONITOR_STATE["allocated_capital"] = allocated
        print(f"💼 Currently Allocated Capital: ₹{allocated:,.2f}")
        return allocated
        
    except Exception as e:
        print(f"❌ Error calculating allocated capital: {e}")
        return 0.0


def calculate_dynamic_trade_quantity(symbol: str, ltp: float) -> int:
    """Calculate dynamic quantity based on capital allocation"""
    
    # Ensure capital allocation is up to date
    if not MONITOR_STATE["total_capital"] or not MONITOR_STATE["last_balance_update"]:
        if not update_capital_allocation():
            print(f"❌ Cannot calculate quantity for {symbol} - capital allocation failed")
            return 0
    
    # Calculate deployment capital and per-trade allocation
    deployment_capital = MONITOR_STATE["total_capital"] * (MONITOR_STATE["deployment_percentage"] / 100.0)
    per_trade_allocation = deployment_capital * (MONITOR_STATE["per_trade_percentage"] / 100.0)
    
    # Calculate currently allocated capital
    allocated_capital = calculate_allocated_capital()
    available_deployment_capital = deployment_capital - allocated_capital
    
    print(f"🧮 Dynamic Quantity Calculation for {symbol}:")
    print(f"   Per-trade allocation (5% of deployment): ₹{per_trade_allocation:,.2f}")
    print(f"   Available deployment capital: ₹{available_deployment_capital:,.2f}")
    
    # Check if we have enough available capital
    if available_deployment_capital < per_trade_allocation:
        print(f"🛑 Insufficient deployment capital for {symbol}")
        print(f"   Need: ₹{per_trade_allocation:,.2f}, Available: ₹{available_deployment_capital:,.2f}")
        return 0
    
    # Calculate quantity based on LTP
    if ltp <= 0:
        print(f"❌ Invalid LTP {ltp} for {symbol}")
        return 0
    
    quantity = int(per_trade_allocation / ltp)
    
    if quantity <= 0:
        print(f"🛑 Calculated quantity is 0 for {symbol} (₹{per_trade_allocation:,.2f} / ₹{ltp:.2f})")
        return 0
    
    # Verify total cost doesn't exceed allocation
    total_cost = quantity * ltp
    if total_cost > per_trade_allocation:
        quantity = int(per_trade_allocation / ltp)  # Recalculate to be safe
        total_cost = quantity * ltp
    
    print(f"✅ Dynamic Quantity for {symbol}: {quantity} shares (₹{total_cost:,.2f})")
    
    return quantity


def should_buy(symbol: str, ltp: float, prev_close: float) -> bool:
    threshold = prev_close * (1 - BUY_GAP_PERCENT / 100.0)
    return ltp <= threshold


def verify_order_execution(order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
    """Verify order execution and get actual fill price"""
    if not KITE or not KITE.kite:
        return None
    
    try:
        order_history = KITE.get_order_history(order_id)
        if not order_history:
            return None
        
        # Get the latest status
        latest_order = order_history[-1]
        status = latest_order.get('status')
        
        if status == 'COMPLETE':
            return {
                'status': 'COMPLETE',
                'average_price': float(latest_order.get('average_price', 0)),
                'filled_quantity': int(latest_order.get('filled_quantity', 0)),
                'order_timestamp': latest_order.get('order_timestamp')
            }
        else:
            return {
                'status': status,
                'pending_quantity': int(latest_order.get('pending_quantity', 0))
            }
    except Exception as e:
        print(f"Error verifying order {order_id}: {e}")
        return None


def check_and_execute_buy(symbol: str, qty: int, dry_run: bool):
    """Enhanced buy logic with robust validation and execution tracking"""
    
    # idempotency: one buy per symbol per day - multiple protection layers
    if symbol in MONITOR_STATE["bought_today"]:
        print(f"🛑 Skipping {symbol}: Already bought today (in memory)")
        return
        
    # Additional protection: Check if position already exists in database
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT COUNT(*) FROM positions WHERE symbol = ? AND status NOT IN ('TARGET_HIT', 'SOLD')", (symbol,))
        if cur.fetchone()[0] > 0:
            print(f"🛑 Skipping {symbol}: Active position already exists in database")
            MONITOR_STATE["bought_today"].add(symbol)  # Add to memory protection too
            return

    # Validate symbol data availability
    prev_close = MONITOR_STATE["last_prev_close"].get(symbol)
    if prev_close is None:
        prev_close = fetch_prev_close(symbol)
        if prev_close is None:
            print(f"❌ Prev close unknown for {symbol}; skipping")
            return
        MONITOR_STATE["last_prev_close"][symbol] = prev_close

    ltp = fetch_ltp(symbol)
    if ltp is None:
        print(f"❌ LTP unavailable for {symbol}")
        return

    # Check buy condition
    if should_buy(symbol, ltp, prev_close):
        gap_percent = ((ltp - prev_close) / prev_close) * 100
        print(f"🎯 BUY TRIGGER: {symbol}")
        print(f"   Previous Close: ₹{prev_close:.2f}")
        print(f"   Current LTP: ₹{ltp:.2f}")
        print(f"   Gap: {gap_percent:.2f}%")
        
        # 🔥 DYNAMIC QUANTITY CALCULATION - Use real capital allocation
        dynamic_qty = calculate_dynamic_trade_quantity(symbol, ltp)
        if dynamic_qty <= 0:
            print(f"🛑 Skipping {symbol}: Dynamic quantity calculation returned 0")
            return
        
        # Use dynamic quantity instead of passed qty parameter
        qty = dynamic_qty
        print(f"📊 Using dynamic quantity: {qty} shares for {symbol}")
        
        if dry_run:
            # Simulate executed price as current LTP
            executed_price = ltp
            order_id = "DRYRUN-" + datetime.utcnow().isoformat()
            save_trade(symbol, qty, "BUY", executed_price, order_id, True, {
                "note": "dry_run simulated buy",
                "gap_percent": gap_percent,
                "prev_close": prev_close
            })
            target = executed_price * (1 + SELL_TARGET_PERCENT / 100.0)
            upsert_position(symbol, qty, executed_price, datetime.utcnow().isoformat(), target, "BOUGHT")
            MONITOR_STATE["bought_today"].add(symbol)
            notify(f"[DRY_RUN] 📊 Bought {qty} {symbol} at ₹{executed_price:.2f} (Gap: {gap_percent:.2f}%); Target: ₹{target:.2f}")
        else:
            # LIVE TRADING - Enhanced execution
            try:
                print(f"🚀 PLACING LIVE BUY ORDER: {qty} x {symbol}")
                
                # Place the order
                resp = KITE.place_market_buy(symbol, qty)
                order_id = resp.get("order_id") if isinstance(resp, dict) else str(resp)
                
                print(f"✅ Order placed successfully! Order ID: {order_id}")
                
                # Wait a moment for order execution
                time.sleep(2)
                
                # Verify order execution
                execution_details = verify_order_execution(order_id, symbol)
                
                if execution_details and execution_details.get('status') == 'COMPLETE':
                    executed_price = execution_details['average_price']
                    filled_qty = execution_details['filled_quantity']
                    
                    print(f"✅ ORDER EXECUTED:")
                    print(f"   Filled Qty: {filled_qty}")
                    print(f"   Average Price: ₹{executed_price:.2f}")
                    
                    # Save trade with actual execution details
                    save_trade(symbol, filled_qty, "BUY", executed_price, order_id, False, {
                        "kite_resp": resp,
                        "execution_details": execution_details,
                        "gap_percent": gap_percent,
                        "prev_close": prev_close
                    })
                    
                    # Calculate target and update position
                    target = executed_price * (1 + SELL_TARGET_PERCENT / 100.0)
                    upsert_position(symbol, filled_qty, executed_price, datetime.utcnow().isoformat(), target, "BOUGHT")
                    MONITOR_STATE["bought_today"].add(symbol)
                    
                    notify(f"🎉 [LIVE] BUY EXECUTED! {filled_qty} x {symbol} @ ₹{executed_price:.2f} | Gap: {gap_percent:.2f}% | Target: ₹{target:.2f} | Order: {order_id}")
                    
                    # Automatically place target sell order
                    try:
                        time.sleep(1)  # Brief pause
                        sell_resp = KITE.place_limit_sell(symbol, filled_qty, round(target, 2))
                        sell_order_id = sell_resp.get("order_id") if isinstance(sell_resp, dict) else str(sell_resp)
                        
                        save_trade(symbol, filled_qty, "SELL_LIMIT_PLACED", round(target, 2), sell_order_id, False, {
                            "kite_resp": sell_resp,
                            "target_for_buy_order": order_id
                        })
                        
                        print(f"🎯 Target sell order placed: {sell_order_id} @ ₹{target:.2f}")
                        notify(f"🎯 Target sell order placed: {filled_qty} x {symbol} @ ₹{target:.2f} | Order: {sell_order_id}")
                        
                    except Exception as sell_error:
                        print(f"❌ Failed to place target sell order: {sell_error}")
                        notify(f"⚠️ Buy executed but failed to place sell order for {symbol}: {sell_error}")
                        
                else:
                    # Order not filled or pending
                    print(f"⏳ Order status: {execution_details.get('status') if execution_details else 'UNKNOWN'}")
                    
                    # Save pending order
                    save_trade(symbol, qty, "BUY_PENDING", ltp, order_id, False, {
                        "kite_resp": resp,
                        "execution_details": execution_details,
                        "gap_percent": gap_percent,
                        "prev_close": prev_close
                    })
                    
                    notify(f"⏳ [LIVE] Buy order placed but pending: {qty} x {symbol} | Order: {order_id}")
                    
            except Exception as e:
                error_msg = f"❌ Buy order FAILED for {symbol}: {str(e)}"
                print(error_msg)
                notify(error_msg)
                
                # Save failed order attempt
                save_trade(symbol, qty, "BUY_FAILED", ltp, "", False, {
                    "error": str(e),
                    "gap_percent": gap_percent,
                    "prev_close": prev_close
                })


def monitor_loop():
    """Enhanced monitoring loop with better error handling and position tracking"""
    loop_count = 0
    
    # Previous close data will be fetched on-demand in the monitoring loop
    print("🚀 Starting live trading monitor - previous close data will be fetched as needed")
    
    while True:
        loop_count += 1
        current_time = datetime.now().strftime("%H:%M:%S")
        
        # Periodic status update
        if loop_count % 60 == 1:  # Every 5 minutes (60 * 5 seconds)
            print(f"🔄 [{current_time}] Monitor Loop #{loop_count} - Watching {len(MONITOR_STATE['symbols'])} symbols")
            print(f"   Mode: {'DRY_RUN' if MONITOR_STATE['dry_run'] else 'LIVE TRADING'}")
            print(f"   Bought today: {list(MONITOR_STATE['bought_today'])}")
        
        symbols = MONITOR_STATE["symbols"]
        
        for symbol in symbols:
            try:
                ltp = fetch_ltp(symbol)
                if ltp is None:
                    continue

                # Check existing positions
                with DB_LOCK:
                    cur = DB.cursor()
                    cur.execute("SELECT qty, avg_buy_price, target_price, status FROM positions WHERE symbol = ?", (symbol,))
                    row = cur.fetchone()

                if row:
                    qty, avg_buy_price, target_price, status = row
                    
                    # Skip if position is already closed
                    if status in ["TARGET_HIT", "SOLD"]:
                        continue
                    
                    current_pnl = (ltp - avg_buy_price) * qty
                    pnl_percent = ((ltp - avg_buy_price) / avg_buy_price) * 100
                    
                    # Check if target reached
                    if ltp >= target_price and status not in ["TARGET_HIT"]:
                        profit = (target_price - avg_buy_price) * qty
                        notify(f"🎯 TARGET HIT! {symbol}: LTP ₹{ltp:.2f} >= Target ₹{target_price:.2f} | Profit: ₹{profit:.2f} (+{pnl_percent:.2f}%)")
                        upsert_position(symbol, qty, avg_buy_price, datetime.utcnow().isoformat(), target_price, "TARGET_HIT")
                    
                    # Check stop loss alert (but don't auto-sell)
                    loss_threshold = avg_buy_price * (1 - LOSS_ALERT_PERCENT / 100.0)
                    if ltp <= loss_threshold and status not in ["ALERTED", "STOP_LOSS_HIT"]:
                        loss = (ltp - avg_buy_price) * qty
                        notify(f"🚨 STOP LOSS ALERT! {symbol}: LTP ₹{ltp:.2f} <= Threshold ₹{loss_threshold:.2f} | Loss: ₹{loss:.2f} ({pnl_percent:.2f}%)")
                        notify(f"🚨 Consider selling {qty} shares of {symbol} manually!")
                        upsert_position(symbol, qty, avg_buy_price, datetime.utcnow().isoformat(), target_price, "ALERTED")
                    
                    # Log position status periodically
                    if loop_count % 120 == 1:  # Every 10 minutes
                        print(f"📊 Position {symbol}: {qty} @ ₹{avg_buy_price:.2f} | Current: ₹{ltp:.2f} | P&L: ₹{current_pnl:.2f} ({pnl_percent:+.2f}%) | Status: {status}")
                        
                else:
                    # No position - check for buy opportunities
                    check_and_execute_buy(symbol, MONITOR_STATE["qty"], MONITOR_STATE["dry_run"])

            except Exception as e:
                error_msg = f"❌ Monitor loop error for {symbol}: {e}"
                print(error_msg)
                if loop_count % 60 == 1:  # Don't spam errors
                    notify(f"Monitor error: {symbol} - {str(e)[:100]}")
        
        time.sleep(POLL_INTERVAL)


# ---- Streamlit UI ----

st.set_page_config(page_title="ETF Gap-Down Trader", layout="wide")
st.title("ETF Gap-Down Trader — Streamlit + Zerodha (Prototype)")

# Access Token Generation Section
with st.expander("🔑 Generate Access Token", expanded=not KITE_ACCESS_TOKEN):
    st.write("Follow these steps to generate a new access token:")
    
    # Step 1: Login URL
    st.subheader("Step 1: Login to Zerodha")
    if KITE_API_KEY:
        login_url = KiteConnect(api_key=KITE_API_KEY).login_url()
        st.markdown(f"Click here to login: [Zerodha Login]({login_url})")
    else:
        st.error("KITE_API_KEY not found in environment variables")
    
    # Step 2: Request Token
    st.subheader("Step 2: Enter Request Token")
    st.write("After login, copy the request token from the redirect URL")
    request_token = st.text_input("Request Token", key="request_token")
    
    if st.button("Generate Access Token"):
        if request_token:
            try:
                kite = KiteConnect(api_key=KITE_API_KEY)
                data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
                access_token = data["access_token"]
                
                # Update .env file
                with open('.env', 'r') as file:
                    lines = file.readlines()
                
                with open('.env', 'w') as file:
                    for line in lines:
                        if line.startswith('KITE_ACCESS_TOKEN='):
                            file.write(f'KITE_ACCESS_TOKEN={access_token}\\n')
                        else:
                            file.write(line)
                
                st.success(f"✅ Access token generated successfully!")
                st.info("The application will reload in 3 seconds...")
                time.sleep(3)
                st.rerun()
            except Exception as e:
                st.error(f"Error generating access token: {str(e)}")
        else:
            st.warning("Please enter the request token")

# Left controls
with st.sidebar:
    st.header("⚙️ Trading Settings")
    
    # Trading Mode - FORCED LIVE TRADING
    dry_run = False  # Always live trading mode
    st.success("🚀 LIVE TRADING MODE - Real money will be used for trades!")
    
    MONITOR_STATE["dry_run"] = dry_run
    
    # 💰 Dynamic Capital Allocation Settings
    st.subheader("💰 Capital Allocation")
    
    # Update capital allocation button
    if st.button("🔄 Refresh Real Balance", type="secondary"):
        update_capital_allocation()
        st.rerun()
    
    # Display current capital information
    if MONITOR_STATE["total_capital"] > 0:
        deployment_capital = MONITOR_STATE["total_capital"] * (MONITOR_STATE["deployment_percentage"] / 100.0)
        reserve_capital = MONITOR_STATE["total_capital"] * (MONITOR_STATE["reserve_percentage"] / 100.0)
        per_trade_allocation = deployment_capital * (MONITOR_STATE["per_trade_percentage"] / 100.0)
        
        st.metric("Total Capital", f"₹{MONITOR_STATE['total_capital']:,.2f}")
        st.metric("Deployment Capital (70%)", f"₹{deployment_capital:,.2f}")
        st.metric("Reserve Capital (30%)", f"₹{reserve_capital:,.2f}")
        st.metric("Per Trade Allocation (5%)", f"₹{per_trade_allocation:,.2f}")
        
        # Show allocated capital
        allocated = calculate_allocated_capital()
        available_deployment = deployment_capital - allocated
        st.metric("Available for Trading", f"₹{available_deployment:,.2f}")
    else:
        st.warning("⚠️ Real balance not loaded. Click 'Refresh Real Balance'")
    
    # Capital allocation parameters (advanced settings)
    with st.expander("� Advanced Capital Settings"):
        deployment_pct = st.slider("Deployment %", 50, 90, int(MONITOR_STATE["deployment_percentage"]))
        per_trade_pct = st.slider("Per Trade %", 1, 15, int(MONITOR_STATE["per_trade_percentage"]))
        
        if deployment_pct != MONITOR_STATE["deployment_percentage"] or per_trade_pct != MONITOR_STATE["per_trade_percentage"]:
            MONITOR_STATE["deployment_percentage"] = float(deployment_pct)
            MONITOR_STATE["reserve_percentage"] = 100.0 - deployment_pct
            MONITOR_STATE["per_trade_percentage"] = float(per_trade_pct)
            st.success(f"Updated: {deployment_pct}% deployment, {per_trade_pct}% per trade")
    
    # Legacy quantity setting (now informational only)
    st.subheader("📊 Position Sizing")
    st.info("✨ Quantities are now calculated dynamically based on capital allocation above")
    
    # Show what quantity would be for reference
    if MONITOR_STATE["total_capital"] > 0:
        st.write("**Sample calculations for current prices:**")
        sample_symbols = MONITOR_STATE["symbols"][:3]  # Show first 3 symbols
        for sym in sample_symbols:
            ltp = fetch_ltp(sym)
            if ltp and ltp > 0:
                deployment_cap = MONITOR_STATE["total_capital"] * (MONITOR_STATE["deployment_percentage"] / 100.0)
                per_trade_alloc = deployment_cap * (MONITOR_STATE["per_trade_percentage"] / 100.0)
                sample_qty = int(per_trade_alloc / ltp)
                st.write(f"• {sym}: {sample_qty} shares @ ₹{ltp:.2f} = ₹{sample_qty * ltp:,.2f}")
    
    qty = 10  # Not used anymore, kept for compatibility
    MONITOR_STATE["qty"] = int(qty)
    
    # Watchlist
    st.subheader("📋 Watchlist")
    wl_input = st.text_area("ETF Symbols (one per line or comma-separated)", 
                           value=",".join(MONITOR_STATE["symbols"]), height=100)
    if st.button("🔄 Update watchlist"):
        # Parse symbols from input
        symbols = []
        for line in wl_input.replace(',', '\n').split('\n'):
            symbol = line.strip().upper()
            if symbol:
                symbols.append(symbol)
        
        MONITOR_STATE["symbols"] = symbols
        # Don't reset bought_today when updating watchlist - it should persist throughout the trading day
        
        # Initialize previous close data for new symbols
        if KITE and KITE.kite:
            for symbol in symbols:
                if symbol not in MONITOR_STATE["last_prev_close"]:
                    prev_close = fetch_prev_close(symbol)
                    if prev_close:
                        MONITOR_STATE["last_prev_close"][symbol] = prev_close
        
        st.success(f"Updated watchlist: {len(symbols)} symbols")
        st.rerun()

    # Strategy parameters
    st.subheader("📈 Strategy Parameters")
    st.write(f"Gap Down Trigger: {BUY_GAP_PERCENT}%")
    st.write(f"Profit Target: {SELL_TARGET_PERCENT}%") 
    st.write(f"Loss Alert: {LOSS_ALERT_PERCENT}%")
    st.write(f"Poll Interval: {POLL_INTERVAL}s")

    st.markdown("---")
    
    # Connection status
    st.subheader("🔌 Connection Status")
    if KITE and KITE.kite:
        st.success("✅ Kite API Connected")
        if not dry_run:
            try:
                margins = KITE.get_margins()
                available_cash = margins.get('equity', {}).get('available', {}).get('live_balance', 0)
                net_balance = margins.get('equity', {}).get('net', 0)
                st.info(f"💰 Available Cash: ₹{available_cash:,.2f} | Net Balance: ₹{net_balance:,.2f}")
            except:
                st.warning("⚠️ Could not fetch margin info")
    else:
        st.error("❌ Kite API Not Connected")
        st.write("Set environment variables:")
        st.code("KITE_API_KEY\nKITE_API_SECRET\nKITE_ACCESS_TOKEN")

    # Telegram status
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        st.success("✅ Telegram Alerts Enabled")
    else:
        st.warning("⚠️ Telegram Not Configured")

    st.markdown("---")
    
    # Control buttons
    st.subheader("🎛️ Controls")
    if st.button("🔄 Reset bought_today flags"):
        MONITOR_STATE["bought_today"] = set()
        st.success("Reset complete - can buy symbols again today")
        st.rerun()
    
    if st.button("📊 Test Telegram"):
        success = send_telegram(f"🧪 Test message from ETF Trader at {datetime.now().strftime('%H:%M:%S')}")
        if success:
            st.success("Telegram test sent!")
        else:
            st.error("Telegram test failed")

    # Emergency stop
    st.markdown("---")
    st.subheader("🚨 Emergency Controls")
    if st.button("🛑 EMERGENCY STOP", type="primary"):
        # This would need a global stop mechanism
        st.error("Emergency stop activated!")
        st.info("Stop the Streamlit app to halt all trading")
    
    # Status info
    st.markdown("---")
    st.subheader("ℹ️ Status")
    st.write(f"**Mode:** {'🛡️ DRY_RUN' if dry_run else '🚀 LIVE'}")
    st.write(f"**Symbols:** {len(MONITOR_STATE['symbols'])}")
    st.write(f"**Bought Today:** {len(MONITOR_STATE['bought_today'])}")
    if MONITOR_STATE['bought_today']:
        st.write("Purchased:", ", ".join(MONITOR_STATE['bought_today']))

# Start monitor thread once
if "monitor_thread_started" not in st.session_state:
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    st.session_state["monitor_thread_started"] = True

# Main table: positions + live LTP
symbols = MONITOR_STATE["symbols"]
rows = []
import time

for i, sym in enumerate(symbols):
    # Fetch previous close if not cached
    prev_close = MONITOR_STATE["last_prev_close"].get(sym)
    if prev_close is None:
        prev_close = fetch_prev_close(sym)
        if prev_close:
            MONITOR_STATE["last_prev_close"][sym] = prev_close
    
    # Fetch current LTP
    ltp = fetch_ltp(sym)
    
    # Debug info (can be removed later)
    if prev_close is None:
        print(f"⚠️ No previous close for {sym}")
    if ltp is None:
        print(f"⚠️ No LTP for {sym}")
    
    # status from DB
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT qty, avg_buy_price, target_price, status FROM positions WHERE symbol = ?", (sym,))
        row = cur.fetchone()
    if row:
        qty_db, avg_buy, target, status = row
        unreal = (ltp - avg_buy) * qty_db if isinstance(ltp, (int, float)) else None
    else:
        qty_db, avg_buy, target, status, unreal = 0, None, None, "WATCHING", None

    pct_vs_prev = None
    if isinstance(prev_close, (int, float)) and isinstance(ltp, (int, float)):
        pct_vs_prev = (ltp - prev_close) / prev_close * 100.0
    rows.append({
        "symbol": sym,
        "prev_close": f"₹{prev_close:.2f}" if isinstance(prev_close, (int, float)) else "-",
        "ltp": f"₹{ltp:.2f}" if isinstance(ltp, (int, float)) else "-",
        "% vs prev_close": f"{pct_vs_prev:.2f}%" if pct_vs_prev is not None else "-",
        "position_qty": qty_db,
        "avg_buy": f"₹{avg_buy:.2f}" if avg_buy is not None else "-",
        "target_price": f"₹{target:.2f}" if target is not None else "-",
        "unrealized_pnl": f"₹{unreal:.2f}" if unreal is not None else "-",
        "status": status,
    })
    
    # Small delay to avoid rate limits when fetching data for multiple symbols
    if i > 0 and i % 3 == 0:
        time.sleep(0.5)

st.subheader("Watchlist")

# Add refresh button and auto-refresh
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🔄 Refresh Data"):
        # Clear cached data to force refresh
        MONITOR_STATE["last_prev_close"].clear()
        st.rerun()

# Show data status
if any(isinstance(MONITOR_STATE["last_prev_close"].get(sym), (int, float)) for sym in symbols):
    st.success(f"✅ Data loaded for {len([s for s in symbols if MONITOR_STATE['last_prev_close'].get(s)])} symbols")
else:
    st.warning("⏳ Loading market data... Click 'Refresh Data' if data doesn't appear")

df = pd.DataFrame(rows)
st.dataframe(df, width='stretch')

# Activity log (last 50 trades)
with DB_LOCK:
    trades_df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC LIMIT 50", DB)
st.subheader("Recent activity")
st.dataframe(trades_df, width='stretch')

# Manual actions
st.subheader("🎮 Manual Trading Actions")

# Safety check for manual trades
if not MONITOR_STATE["dry_run"] and (not KITE or not KITE.kite):
    st.error("❌ Manual trading disabled: Kite API not connected")
else:
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("### 🛒 Manual BUY")
        symbol_manual = st.text_input("Symbol (NSE tradingsymbol)", value="", key="manual_buy_symbol")
        qty_manual = st.number_input("Quantity", value=MONITOR_STATE["qty"], min_value=1, key="manual_qty")
        
        # Show current quote if available
        if symbol_manual and KITE:
            quote = KITE.quote(symbol_manual)
            if quote:
                ltp = quote.get('last_price', 0)
                st.info(f"Current LTP: ₹{ltp:.2f}")
                estimated_cost = ltp * qty_manual
                st.info(f"Estimated Cost: ₹{estimated_cost:,.2f}")
        
        buy_button_text = "🛡️ Simulate BUY" if MONITOR_STATE["dry_run"] else "🚀 PLACE BUY ORDER"
        
        if st.button(buy_button_text, type="primary"):
            if not symbol_manual:
                st.error("Enter a symbol")
            elif symbol_manual in MONITOR_STATE["bought_today"]:
                st.error(f"❌ Already bought {symbol_manual} today! Only one order per symbol per day is allowed.")
            else:
                if MONITOR_STATE["dry_run"]:
                    # Get current LTP for simulation
                    ltp = 0.0
                    if KITE:
                        quote = KITE.quote(symbol_manual)
                        if quote:
                            ltp = quote.get('last_price', 0)
                    
                    save_trade(symbol_manual, int(qty_manual), "BUY", ltp, "DRYRUN-MANUAL", True, {
                        "note": "manual dryrun buy",
                        "estimated_cost": ltp * qty_manual
                    })
                    
                    # Mark as bought today to prevent multiple orders
                    MONITOR_STATE["bought_today"].add(symbol_manual)
                    
                    st.success(f"🛡️ [DRY_RUN] Buy simulated: {qty_manual} x {symbol_manual} @ ₹{ltp:.2f}")
                else:
                    try:
                        with st.spinner("Placing buy order..."):
                            resp = KITE.place_market_buy(symbol_manual, int(qty_manual))
                            order_id = resp.get("order_id") if isinstance(resp, dict) else str(resp)
                            
                            # Wait and verify execution
                            time.sleep(2)
                            execution = verify_order_execution(order_id, symbol_manual)
                            
                            if execution and execution.get('status') == 'COMPLETE':
                                avg_price = execution['average_price']
                                filled_qty = execution['filled_quantity']
                                total_cost = avg_price * filled_qty
                                
                                save_trade(symbol_manual, filled_qty, "BUY", avg_price, order_id, False, {
                                    "kite_resp": resp,
                                    "execution_details": execution
                                })
                                
                                st.success(f"✅ BUY EXECUTED: {filled_qty} x {symbol_manual} @ ₹{avg_price:.2f}")
                                st.info(f"Total Cost: ₹{total_cost:,.2f} | Order ID: {order_id}")
                                
                                # Auto-create position entry
                                target = avg_price * (1 + SELL_TARGET_PERCENT / 100.0)
                                upsert_position(symbol_manual, filled_qty, avg_price, datetime.utcnow().isoformat(), target, "BOUGHT")
                                
                                # Mark as bought today to prevent multiple orders
                                MONITOR_STATE["bought_today"].add(symbol_manual)
                                
                            else:
                                save_trade(symbol_manual, int(qty_manual), "BUY_PENDING", 0.0, order_id, False, {
                                    "kite_resp": resp,
                                    "execution_details": execution
                                })
                                st.warning(f"⏳ Order placed but pending: {order_id}")
                                
                    except Exception as e:
                        st.error(f"❌ Buy order failed: {str(e)}")
                        save_trade(symbol_manual, int(qty_manual), "BUY_FAILED", 0.0, "", False, {"error": str(e)})

    with col2:
        st.write("### 📤 Manual SELL")
        symbol_sell = st.text_input("Symbol to sell", value="", key="manual_sell_symbol")
        
        # Auto-populate from positions
        positions_df = load_positions_df()
        if not positions_df.empty:
            available_positions = positions_df[positions_df['qty'] > 0]['symbol'].tolist()
            if available_positions:
                symbol_sell = st.selectbox("Or select from positions:", [""] + available_positions, key="position_select")
        
        qty_sell = st.number_input("Quantity to sell", value=0, min_value=0, key="manual_sell_qty")
        
        # Order type selection
        sell_type = st.radio("Order Type:", ["Market", "Limit"], key="sell_type")
        price_sell = 0.0
        
        if sell_type == "Limit":
            price_sell = st.number_input("Limit price", value=0.0, format="%.2f", key="limit_price")
            if symbol_sell and KITE:
                quote = KITE.quote(symbol_sell)
                if quote:
                    ltp = quote.get('last_price', 0)
                    st.info(f"Current LTP: ₹{ltp:.2f}")
        
        # Show position details if available
        if symbol_sell and not positions_df.empty:
            pos_data = positions_df[positions_df['symbol'] == symbol_sell]
            if not pos_data.empty:
                pos = pos_data.iloc[0]
                st.info(f"Position: {pos['qty']} shares @ ₹{pos['avg_buy_price']:.2f}")
                if qty_sell > pos['qty']:
                    st.error(f"Cannot sell {qty_sell} - only {pos['qty']} available")

        sell_button_text = "🛡️ Simulate SELL" if MONITOR_STATE["dry_run"] else f"🚀 PLACE {sell_type.upper()} SELL"
        
        if st.button(sell_button_text, type="secondary"):
            if not symbol_sell:
                st.error("Enter symbol to sell")
            elif qty_sell <= 0:
                st.error("Enter quantity > 0")
            else:
                if MONITOR_STATE["dry_run"]:
                    # Simulate sell
                    ltp = price_sell if sell_type == "Limit" else 0.0
                    if ltp == 0.0 and KITE:
                        quote = KITE.quote(symbol_sell)
                        if quote:
                            ltp = quote.get('last_price', 0)
                    
                    save_trade(symbol_sell, int(qty_sell), "SELL", ltp, "DRYRUN-MANUAL-SELL", True, {
                        "note": f"manual dryrun {sell_type.lower()} sell",
                        "order_type": sell_type.lower()
                    })
                    st.success(f"🛡️ [DRY_RUN] {sell_type} sell simulated: {qty_sell} x {symbol_sell}")
                else:
                    try:
                        with st.spinner(f"Placing {sell_type.lower()} sell order..."):
                            if sell_type == "Limit":
                                resp = KITE.place_limit_sell(symbol_sell, int(qty_sell), float(price_sell))
                            else:
                                resp = KITE.place_market_sell(symbol_sell, int(qty_sell))
                                
                            order_id = resp.get("order_id") if isinstance(resp, dict) else str(resp)
                            
                            save_trade(symbol_sell, int(qty_sell), f"SELL_{sell_type.upper()}", float(price_sell) if sell_type == "Limit" else 0.0, order_id, False, {
                                "kite_resp": resp,
                                "order_type": sell_type.lower()
                            })
                            
                            st.success(f"✅ {sell_type} sell order placed: {order_id}")
                            
                    except Exception as e:
                        st.error(f"❌ Sell order failed: {str(e)}")

# Quick actions for positions
positions_df = load_positions_df()  # Load positions for quick actions
if not positions_df.empty:
    st.subheader("⚡ Quick Position Actions")
    
    for _, pos in positions_df.iterrows():
        if pos['qty'] > 0:
            col1, col2, col3, col4 = st.columns([2, 2, 2, 2])
            
            with col1:
                st.write(f"**{pos['symbol']}**")
                st.write(f"{pos['qty']} @ ₹{pos['avg_buy_price']:.2f}")
            
            with col2:
                if KITE:
                    quote = KITE.quote(pos['symbol'])
                    if quote:
                        ltp = quote.get('last_price', 0)
                        pnl = (ltp - pos['avg_buy_price']) * pos['qty']
                        pnl_pct = ((ltp - pos['avg_buy_price']) / pos['avg_buy_price']) * 100
                        st.write(f"LTP: ₹{ltp:.2f}")
                        color = "green" if pnl >= 0 else "red"
                        st.markdown(f"<span style='color: {color}'>P&L: ₹{pnl:.2f} ({pnl_pct:+.1f}%)</span>", unsafe_allow_html=True)
            
            with col3:
                if st.button(f"🎯 Sell @ Target", key=f"target_{pos['symbol']}"):
                    if not MONITOR_STATE["dry_run"]:
                        try:
                            resp = KITE.place_limit_sell(pos['symbol'], pos['qty'], pos['target_price'])
                            st.success(f"Target sell order placed: {resp.get('order_id')}")
                        except Exception as e:
                            st.error(f"Failed: {e}")
                    else:
                        st.info("DRY_RUN: Would place target sell order")
            
            with col4:
                if st.button(f"🚨 Market Sell", key=f"market_{pos['symbol']}"):
                    if not MONITOR_STATE["dry_run"]:
                        try:
                            resp = KITE.place_market_sell(pos['symbol'], pos['qty'])
                            st.success(f"Market sell order placed: {resp.get('order_id')}")
                        except Exception as e:
                            st.error(f"Failed: {e}")
                    else:
                        st.info("DRY_RUN: Would place market sell order")

st.markdown("---")
st.caption("Note: This is a prototype. Always test with DRY_RUN and testnet keys first. The app uses polling to fetch LTP and prev close; for production prefer KiteTicker websocket and robust order verification.")

# --- Enhanced Dashboard Table ---
# Place this in the main Streamlit UI section where the main table is rendered
st.markdown("---")
st.subheader("📈 Live ETF Market Data (Kite Style)")

# Fetch and display real data for all ETFs in the watchlist
data_rows = []
for symbol in MONITOR_STATE["symbols"]:
    ltp = fetch_ltp(symbol)
    prev_close = fetch_prev_close(symbol)
    gap = None
    if ltp is not None and prev_close is not None:
        gap = ((ltp - prev_close) / prev_close) * 100
    else:
        gap = None
    # Try to get volume and OHLC if available
    quote = KITE.quote(symbol) if KITE and KITE.kite else None
    ohlc = quote.get(f"NSE:{symbol}", {}).get("ohlc", {}) if quote else {}
    volume = quote.get(f"NSE:{symbol}", {}).get("volume", None) if quote else None
    data_rows.append({
        "Symbol": symbol,
        "LTP": ltp,
        "Prev Close": prev_close,
        "Gap %": f"{gap:.2f}%" if gap is not None else "-",
        "Open": ohlc.get("open", "-"),
        "High": ohlc.get("high", "-"),
        "Low": ohlc.get("low", "-"),
        "Volume": volume if volume is not None else "-",
    })

# Display as a DataFrame/table
import pandas as pd
df = pd.DataFrame(data_rows)
st.dataframe(df, use_container_width=True)

# --- ETF Candlestick Dashboard ---
st.markdown("---")
st.subheader("📊 ETF Candlestick & Volume Charts (Kite Style)")

selected_etf = st.selectbox("Select ETF to view chart:", MONITOR_STATE["symbols"])

ohlc_df = fetch_ohlc_history(selected_etf)
if ohlc_df is not None and not ohlc_df.empty:
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=ohlc_df['date'],
        open=ohlc_df['open'],
        high=ohlc_df['high'],
        low=ohlc_df['low'],
        close=ohlc_df['close'],
        name='Candles',
        increasing_line_color='green', decreasing_line_color='red',
    ))
    fig.add_trace(go.Bar(
        x=ohlc_df['date'],
        y=ohlc_df['volume'],
        name='Volume',
        marker_color='blue',
        opacity=0.3,
        yaxis='y2',
    ))
    fig.update_layout(
        xaxis_rangeslider_visible=False,
        yaxis_title='Price',
        yaxis2=dict(title='Volume', overlaying='y', side='right', showgrid=False),
        title=f"{selected_etf} - Candlestick & Volume",
        height=600,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning(f"No OHLC data available for {selected_etf}.")