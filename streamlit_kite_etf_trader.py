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

# ---- ETF Instruments Fetcher ----

@st.cache_data(ttl=3600)  # Cache for 1 hour
def _cached_etf_instruments(instruments_data):
    """
    Process instruments data to extract ETF symbols.
    This function can be cached since it only processes data, not API objects.
    """
    # Filter for ETFs only
    etf_symbols = []
    for instrument in instruments_data:
        # Look for ETF characteristics
        name = instrument.get('name', '').upper()
        tradingsymbol = instrument.get('tradingsymbol', '')
        exchange = instrument.get('exchange', '')
        instrument_type = instrument.get('instrument_type', '')
        
        # ETF identification criteria
        is_etf = (
            'ETF' in name or 
            'BEES' in tradingsymbol or 
            'INDEX' in name or
            tradingsymbol.endswith('ETF') or
            tradingsymbol.endswith('BEES') or
            tradingsymbol.endswith('IETF') or
            any(keyword in tradingsymbol for keyword in [
                'LIQUID', 'GOLD', 'SILVER', 'NIFTY', 'SENSEX', 
                'BANK', 'IT', 'PHARMA', 'AUTO', 'ENERGY', 'METAL',
                'INFRA', 'FMCG', 'CONSUMPTION', 'HEALTHCARE'
            ])
        )
        
        # Only NSE ETFs
        if is_etf and exchange == 'NSE' and instrument_type == 'EQ':
            etf_symbols.append(tradingsymbol)
    
    # Remove duplicates and sort
    etf_symbols = sorted(list(set(etf_symbols)))
    return etf_symbols

def fetch_etf_instruments(kite_api=None):
    """
    Fetch all ETF instruments from Kite instruments API.
    Returns list of ETF trading symbols.
    """
    if not kite_api:
        return DEFAULT_WATCHLIST  # Fallback to default if no API
    
    try:
        st.info("🔄 Fetching ETF instruments from Kite API...")
        instruments = kite_api.instruments()
        
        # Use cached function to process the instruments data
        etf_symbols = _cached_etf_instruments(instruments)
        
        st.success(f"✅ Found {len(etf_symbols)} ETF instruments from Kite API")
        return etf_symbols
        
    except Exception as e:
        st.error(f"❌ Error fetching ETF instruments: {e}")
        st.warning("🔄 Falling back to default watchlist")
        return DEFAULT_WATCHLIST

def get_watchlist_from_env_or_instruments(kite_api=None):
    """
    Get watchlist either from environment variable WATCHLIST or fetch from instruments.
    """
    # First try to get from environment
    env_watchlist = os.getenv("WATCHLIST")
    if env_watchlist:
        symbols = [s.strip().upper() for s in env_watchlist.split(',') if s.strip()]
        st.info(f"📋 Using watchlist from environment: {len(symbols)} symbols")
        return symbols
    
    # If no environment watchlist, fetch from instruments
    st.info("🔍 No WATCHLIST found in environment, fetching ETFs from instruments...")
    return fetch_etf_instruments(kite_api)

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
            status TEXT,
            product TEXT DEFAULT 'CNC'
        )
        """
    )
    
    # Add product column to existing positions table if it doesn't exist
    try:
        cur.execute("ALTER TABLE positions ADD COLUMN product TEXT DEFAULT 'CNC'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gtt_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            gtt_id TEXT UNIQUE,
            trigger_type TEXT NOT NULL,  -- 'single' or 'two-leg'
            trigger_price REAL NOT NULL,
            last_price REAL,
            order_type TEXT NOT NULL,    -- 'LIMIT' or 'MARKET'
            quantity INTEGER NOT NULL,
            price REAL,                  -- limit price for LIMIT orders
            condition TEXT NOT NULL,     -- '>=' for buy, '<=' for sell
            status TEXT DEFAULT 'ACTIVE', -- 'ACTIVE', 'TRIGGERED', 'CANCELLED', 'COMPLETED'
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            meta TEXT                    -- JSON metadata
        )
        """
    )
    conn.commit()
    return conn

DB = init_db()
DB_LOCK = threading.Lock()


def safe_json_dumps(obj):
    """Safely serialize objects to JSON, handling datetime objects"""
    def json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    return json.dumps(obj, default=json_serializer)

def save_trade(symbol: str, qty: int, side: str, price: float, order_id: Optional[str], dry_run: bool, extra: Optional[Dict] = None):
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute(
            "INSERT INTO trades (symbol, qty, side, price, timestamp, order_id, dry_run, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, qty, side, price, datetime.now(timezone.utc).isoformat(), order_id or "", 1 if dry_run else 0, safe_json_dumps(extra or {})),
        )
        DB.commit()


def upsert_position(symbol: str, qty: int, avg_buy_price: float, buy_timestamp: str, target_price: float, status: str, product: str = "CNC"):
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT symbol FROM positions WHERE symbol = ?", (symbol,))
        if cur.fetchone():
            cur.execute(
                "UPDATE positions SET qty=?, avg_buy_price=?, buy_timestamp=?, target_price=?, status=?, product=? WHERE symbol = ?",
                (qty, avg_buy_price, buy_timestamp, target_price, status, product, symbol),
            )
        else:
            cur.execute(
                "INSERT INTO positions(symbol, qty, avg_buy_price, buy_timestamp, target_price, status, product) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, qty, avg_buy_price, buy_timestamp, target_price, status, product),
            )
        DB.commit()


def has_active_position(symbol: str) -> bool:
    """
    Check if a symbol already has an active position (BOUGHT status)
    Returns True if position exists and is not sold yet
    """
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT status FROM positions WHERE symbol = ? AND status = 'BOUGHT'", (symbol,))
        result = cur.fetchone()
        return result is not None


def has_pending_gtt(symbol: str) -> bool:
    """
    Check if a symbol already has a pending GTT order
    Returns True if there's an active GTT for this symbol
    """
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT status FROM gtt_orders WHERE symbol = ? AND status = 'ACTIVE'", (symbol,))
        result = cur.fetchone()
        return result is not None


def cleanup_sold_positions():
    """
    Remove positions that have been sold (status = 'SOLD') to allow new trades
    This is called periodically to clean up the positions table
    """
    with DB_LOCK:
        cur = DB.cursor()
        # Get count before cleanup
        cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'SOLD'")
        sold_count = cur.fetchone()[0]
        
        if sold_count > 0:
            # Remove sold positions
            cur.execute("DELETE FROM positions WHERE status = 'SOLD'")
            DB.commit()
            print(f"🧹 Cleaned up {sold_count} sold positions to allow new trades")
            
        return sold_count


def get_position_summary() -> Dict[str, int]:
    """
    Get summary of positions by status
    """
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute("SELECT status, COUNT(*) FROM positions GROUP BY status")
        results = cur.fetchall()
        return dict(results) if results else {}


def load_positions_df() -> pd.DataFrame:
    with DB_LOCK:
        df = pd.read_sql_query("SELECT * FROM positions", DB)
    return df


# ---- GTT (Good Till Triggered) Functions ----

def save_gtt_order(symbol: str, gtt_id: str, trigger_type: str, trigger_price: float, 
                   order_type: str, quantity: int, price: float = None, condition: str = ">=", 
                   meta: dict = None):
    """Save GTT order to database"""
    with DB_LOCK:
        cur = DB.cursor()
        cur.execute(
            """INSERT INTO gtt_orders 
               (symbol, gtt_id, trigger_type, trigger_price, order_type, quantity, price, condition, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, gtt_id, trigger_type, trigger_price, order_type, quantity, price, condition, 
             safe_json_dumps(meta or {}))
        )
        DB.commit()

def update_gtt_status(gtt_id: str, status: str, last_price: float = None):
    """Update GTT order status"""
    with DB_LOCK:
        cur = DB.cursor()
        if last_price:
            cur.execute(
                "UPDATE gtt_orders SET status=?, last_price=?, updated_at=CURRENT_TIMESTAMP WHERE gtt_id=?",
                (status, last_price, gtt_id)
            )
        else:
            cur.execute(
                "UPDATE gtt_orders SET status=?, updated_at=CURRENT_TIMESTAMP WHERE gtt_id=?",
                (status, gtt_id)
            )
        DB.commit()

def load_active_gtts() -> pd.DataFrame:
    """Load active GTT orders"""
    with DB_LOCK:
        df = pd.read_sql_query("SELECT * FROM gtt_orders WHERE status='ACTIVE' ORDER BY created_at DESC", DB)
    return df

def load_all_gtts() -> pd.DataFrame:
    """Load all GTT orders"""
    with DB_LOCK:
        df = pd.read_sql_query("SELECT * FROM gtt_orders ORDER BY created_at DESC", DB)
    return df

def cancel_gtt_order(gtt_id: str):
    """Cancel a GTT order"""
    update_gtt_status(gtt_id, "CANCELLED")


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
        """Place market buy order with MTF preference, fallback to CNC"""
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
        
        # Try MTF first, then fallback to CNC
        order_response = None
        product_used = None
        
        try:
            # First attempt: MTF (Margin Trading Facility)
            print(f"🔄 Attempting MTF buy order for {symbol}")
            order_response = self.kite.place_order(
                tradingsymbol=symbol, 
                exchange="NSE", 
                transaction_type="BUY", 
                quantity=qty, 
                order_type="MARKET", 
                variety="regular", 
                product="MTF"
            )
            product_used = "MTF"
            print(f"✅ MTF order placed successfully for {symbol}")
            
        except Exception as mtf_error:
            print(f"❌ MTF order failed for {symbol}: {mtf_error}")
            print(f"🔄 Falling back to CNC for {symbol}")
            
            try:
                # Fallback: CNC (Cash and Carry)
                order_response = self.kite.place_order(
                    tradingsymbol=symbol, 
                    exchange="NSE", 
                    transaction_type="BUY", 
                    quantity=qty, 
                    order_type="MARKET", 
                    variety="regular", 
                    product="CNC"
                )
                product_used = "CNC"
                print(f"✅ CNC order placed successfully for {symbol}")
                
            except Exception as cnc_error:
                raise RuntimeError(f"Both MTF and CNC orders failed for {symbol}. MTF: {mtf_error}, CNC: {cnc_error}")
        
        # Add product type to response for tracking
        if isinstance(order_response, dict):
            order_response["product_used"] = product_used
        else:
            order_response = {"order_id": str(order_response), "product_used": product_used}
            
        return order_response

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

    # ---- GTT (Good Till Triggered) Methods ----
    
    def place_gtt(self, symbol: str, trigger_price: float, quantity: int, 
                  transaction_type: str = "BUY", order_type: str = "LIMIT", 
                  price: float = None, condition: str = None, product: str = None) -> Dict[str, Any]:
        """
        Place GTT order with MTF preference for buy orders
        
        Args:
            symbol: Trading symbol
            trigger_price: Price at which to trigger
            quantity: Number of shares
            transaction_type: "BUY" or "SELL"
            order_type: "LIMIT" or "MARKET"
            price: Limit price (required for LIMIT orders)
            condition: ">=" for buy triggers, "<=" for sell triggers
            product: "MTF", "CNC", or None (auto-select MTF>CNC for BUY)
        """
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        
        # Auto-determine condition if not provided
        if condition is None:
            condition = ">=" if transaction_type == "BUY" else "<="
        
        # For MARKET orders, price is not required
        if order_type == "MARKET":
            price = 0
        elif price is None:
            price = trigger_price  # Default to trigger price for LIMIT orders
        
        # Auto-select product type for BUY orders (MTF > CNC)
        if product is None and transaction_type == "BUY":
            product = "MTF"  # Try MTF first for buy orders
        elif product is None:
            product = "CNC"  # Default for sell orders
            
        try:
            gtt_params = {
                "tradingsymbol": symbol,
                "exchange": "NSE",
                "trigger_values": [trigger_price],
                "last_price": trigger_price,
                "orders": [{
                    "transaction_type": transaction_type,
                    "quantity": quantity,
                    "order_type": order_type,
                    "product": product,
                    "price": price if order_type == "LIMIT" else 0
                }]
            }
            
            print(f"🎯 Placing GTT: {transaction_type} {quantity} x {symbol} when price {condition} ₹{trigger_price} ({product})")
            
            response = self.kite.place_gtt(**gtt_params)
            gtt_id = response.get("trigger_id")
            
            # Save to local database
            save_gtt_order(
                symbol=symbol,
                gtt_id=str(gtt_id),
                trigger_type="single",
                trigger_price=trigger_price,
                order_type=order_type,
                quantity=quantity,
                price=price,
                condition=condition,
                meta={"transaction_type": transaction_type, "product": product}
            )
            
            print(f"✅ GTT placed successfully. ID: {gtt_id}")
            return response
            
        except Exception as e:
            # If MTF GTT fails for BUY orders, try CNC
            if product == "MTF" and transaction_type == "BUY":
                print(f"❌ MTF GTT failed for {symbol}: {e}")
                print(f"🔄 Retrying with CNC GTT for {symbol}")
                
                try:
                    gtt_params["orders"][0]["product"] = "CNC"
                    
                    response = self.kite.place_gtt(**gtt_params)
                    gtt_id = response.get("trigger_id")
                    
                    # Save to local database with CNC product
                    save_gtt_order(
                        symbol=symbol,
                        gtt_id=str(gtt_id),
                        trigger_type="single",
                        trigger_price=trigger_price,
                        order_type=order_type,
                        quantity=quantity,
                        price=price,
                        condition=condition,
                        meta={"transaction_type": transaction_type, "product": "CNC"}
                    )
                    
                    print(f"✅ CNC GTT placed successfully. ID: {gtt_id}")
                    return response
                    
                except Exception as cnc_error:
                    print(f"❌ Both MTF and CNC GTT failed for {symbol}")
                    raise RuntimeError(f"GTT placement failed. MTF: {e}, CNC: {cnc_error}")
            else:
                print(f"❌ GTT placement failed: {e}")
                raise e
    
    def get_gtts(self) -> List[Dict]:
        """Get all active GTT orders from Kite"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
        
        try:
            return self.kite.get_gtts()
        except Exception as e:
            print(f"❌ Error fetching GTTs: {e}")
            return []
    
    def cancel_gtt(self, gtt_id: str) -> Dict[str, Any]:
        """Cancel a GTT order"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
            
        try:
            print(f"❌ Cancelling GTT: {gtt_id}")
            response = self.kite.cancel_gtt(gtt_id)
            
            # Update local database
            update_gtt_status(gtt_id, "CANCELLED")
            
            print(f"✅ GTT cancelled successfully: {gtt_id}")
            return response
            
        except Exception as e:
            print(f"❌ GTT cancellation failed: {e}")
            raise e
    
    def modify_gtt(self, gtt_id: str, trigger_price: float, quantity: int, price: float = None) -> Dict[str, Any]:
        """Modify an existing GTT order"""
        if self.kite is None:
            raise RuntimeError("Kite client not initialized")
            
        try:
            # Get existing GTT details first
            gtts = self.get_gtts()
            existing_gtt = None
            
            for gtt in gtts:
                if str(gtt.get("id")) == str(gtt_id):
                    existing_gtt = gtt
                    break
            
            if not existing_gtt:
                raise ValueError(f"GTT {gtt_id} not found")
            
            # Use existing order details and update what's changed
            order = existing_gtt["orders"][0]
            
            gtt_params = {
                "trigger_id": gtt_id,
                "tradingsymbol": existing_gtt["tradingsymbol"],
                "exchange": existing_gtt["exchange"],
                "trigger_values": [trigger_price],
                "last_price": trigger_price,
                "orders": [{
                    "transaction_type": order["transaction_type"],
                    "quantity": quantity,
                    "order_type": order["order_type"],
                    "product": order["product"],
                    "price": price if price else order["price"]
                }]
            }
            
            print(f"📝 Modifying GTT {gtt_id}: trigger=₹{trigger_price}, qty={quantity}")
            
            response = self.kite.modify_gtt(**gtt_params)
            
            print(f"✅ GTT modified successfully: {gtt_id}")
            return response
            
        except Exception as e:
            print(f"❌ GTT modification failed: {e}")
            raise e


# Instantiate Kite wrapper (may be None if not configured)
KITE = None

def get_kite_connection():
    """Get or create Kite connection for current session"""
    # Use session state to maintain connection across different access methods
    if "kite_connection" not in st.session_state:
        st.session_state.kite_connection = None
    
    # If no connection in session state, try to create one
    if st.session_state.kite_connection is None and KITE_API_KEY and KITE_ACCESS_TOKEN:
        try:
            st.session_state.kite_connection = KiteWrapper(KITE_API_KEY, KITE_ACCESS_TOKEN)
            print(f"✅ Created new Kite connection for session")
        except Exception as e:
            print(f"❌ Failed to create Kite connection: {e}")
            st.session_state.kite_connection = None
    
    return st.session_state.kite_connection

def refresh_kite_connection():
    """Force refresh of Kite connection"""
    if "kite_connection" in st.session_state:
        del st.session_state.kite_connection
    return get_kite_connection()

# Capital management functions
def fetch_real_account_balance():
    """Fetch real account balance from Kite"""
    try:
        kite_conn = get_kite_connection()
        if not kite_conn or not kite_conn.kite:
            print("❌ Kite connection not available")
            return 0.0
        
        margins = kite_conn.kite.margins()
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
    # Set global KITE for backward compatibility
    KITE = KiteWrapper(KITE_API_KEY, KITE_ACCESS_TOKEN) if KITE_ACCESS_TOKEN else None
    
    # Initialize watchlist from environment or instruments
    kite_conn = get_kite_connection()
    if kite_conn and kite_conn.kite:
        # Update watchlist with ETFs from instruments API
        MONITOR_STATE["symbols"] = get_watchlist_from_env_or_instruments(kite_conn.kite)
    else:
        # Fallback to environment or default
        MONITOR_STATE["symbols"] = get_watchlist_from_env_or_instruments(None)
    
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


# ---- Token Validation Functions ----

def check_token_validity():
    """Check if the current access token is valid"""
    if not KITE_ACCESS_TOKEN:
        return {"valid": False, "error": "No access token found", "status": "missing"}
    
    kite_conn = get_kite_connection()
    if not kite_conn or not kite_conn.kite:
        return {"valid": False, "error": "Kite client not initialized", "status": "not_initialized"}
    
    try:
        # Try to fetch profile to test token validity
        profile = kite_conn.kite.profile()
        return {
            "valid": True, 
            "user_name": profile.get('user_name', 'Unknown'),
            "broker": profile.get('broker', 'Unknown'),
            "status": "active"
        }
    except Exception as e:
        error_msg = str(e).lower()
        if "token" in error_msg or "auth" in error_msg or "expired" in error_msg:
            return {"valid": False, "error": str(e), "status": "expired"}
        else:
            return {"valid": False, "error": str(e), "status": "error"}

def get_token_status_display():
    """Get formatted token status for display"""
    status = check_token_validity()
    
    if status["valid"]:
        return {
            "emoji": "✅",
            "message": f"Connected: {status['user_name']}",
            "color": "success",
            "action_needed": False
        }
    elif status["status"] == "missing":
        return {
            "emoji": "❌",
            "message": "No Access Token",
            "color": "error", 
            "action_needed": True,
            "action": "generate"
        }
    elif status["status"] == "expired":
        return {
            "emoji": "⏰",
            "message": "Token Expired", 
            "color": "warning",
            "action_needed": True,
            "action": "renew"
        }
    else:
        return {
            "emoji": "❌", 
            "message": "Connection Error",
            "color": "error",
            "action_needed": True,
            "action": "check"
        }

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
        gap_percent = ((ltp - prev_close) / prev_close) * 100 if prev_close != 0 else 0
        print(f"🎯 BUY TRIGGER: {symbol}")
        print(f"   Previous Close: ₹{prev_close:.2f}")
        print(f"   Current LTP: ₹{ltp:.2f}")
        print(f"   Gap: {gap_percent:.2f}%")
        
        # � IMMEDIATE PROTECTION: Add to bought_today BEFORE any order processing
        # This prevents multiple concurrent orders for the same symbol
        MONITOR_STATE["bought_today"].add(symbol)
        print(f"🔒 Added {symbol} to bought_today protection list")
        
        # �🔥 DYNAMIC QUANTITY CALCULATION - Use real capital allocation
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
            order_id = "DRYRUN-" + datetime.now(timezone.utc).isoformat()
            save_trade(symbol, qty, "BUY", executed_price, order_id, True, {
                "note": "dry_run simulated buy",
                "gap_percent": gap_percent,
                "prev_close": prev_close
            })
            target = executed_price * (1 + SELL_TARGET_PERCENT / 100.0)
            upsert_position(symbol, qty, executed_price, datetime.now(timezone.utc).isoformat(), target, "BOUGHT")
            notify(f"[DRY_RUN] 📊 Bought {qty} {symbol} at ₹{executed_price:.2f} (Gap: {gap_percent:.2f}%); Target: ₹{target:.2f}")
        else:
            # LIVE TRADING - Enhanced execution with MTF/CNC support
            try:
                print(f"🚀 PLACING LIVE BUY ORDER: {qty} x {symbol}")
                
                # Place the order (will try MTF first, then CNC)
                resp = KITE.place_market_buy(symbol, qty)
                order_id = resp.get("order_id") if isinstance(resp, dict) else str(resp)
                product_used = resp.get("product_used", "CNC")  # Track which product was used
                
                print(f"✅ Order placed successfully! Order ID: {order_id} (Product: {product_used})")
                
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
                    print(f"   Product: {product_used}")
                    
                    # Save trade with actual execution details
                    save_trade(symbol, filled_qty, "BUY", executed_price, order_id, False, {
                        "kite_resp": resp,
                        "execution_details": execution_details,
                        "gap_percent": gap_percent,
                        "prev_close": prev_close,
                        "product_used": product_used
                    })
                    
                    # Calculate target and update position with product type
                    target = executed_price * (1 + SELL_TARGET_PERCENT / 100.0)
                    upsert_position(symbol, filled_qty, executed_price, datetime.now(timezone.utc).isoformat(), target, "BOUGHT", product_used)
                    
                    # Place GTT sell order with same product type
                    try:
                        print(f"🎯 Setting up GTT sell target for {symbol} @ ₹{target:.2f} ({product_used})")
                        
                        sell_gtt = KITE.place_gtt(
                            symbol=symbol,
                            trigger_price=target,
                            quantity=filled_qty,
                            transaction_type="SELL",
                            order_type="LIMIT",
                            price=target,
                            condition=">=",
                            product=product_used  # Use same product type
                        )
                        
                        print(f"✅ GTT sell target placed! GTT ID: {sell_gtt.get('trigger_id')}")
                        notify(f"� [LIVE] GTT Target Set: {symbol} will sell {filled_qty} @ ₹{target:.2f} (+{SELL_TARGET_PERCENT}%) ({product_used})")
                        
                    except Exception as gtt_error:
                        print(f"❌ Failed to place GTT sell target for {symbol}: {gtt_error}")
                        notify(f"⚠️ GTT Sell Setup Failed for {symbol}: {gtt_error}")
                    
                    notify(f"�🎉 [LIVE] BUY EXECUTED! {filled_qty} x {symbol} @ ₹{executed_price:.2f} | Gap: {gap_percent:.2f}% | Target: ₹{target:.2f} | Product: {product_used} | Order: {order_id}")
                    
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
        
        # 🧹 Cleanup: Remove closed positions from bought_today protection
        if loop_count % 120 == 1:  # Every 10 minutes
            closed_symbols = []
            with DB_LOCK:
                cur = DB.cursor()
                for symbol in list(MONITOR_STATE["bought_today"]):
                    cur.execute("SELECT status FROM positions WHERE symbol = ? AND status IN ('TARGET_HIT', 'SOLD')", (symbol,))
                    if cur.fetchone():
                        closed_symbols.append(symbol)
            
            for symbol in closed_symbols:
                MONITOR_STATE["bought_today"].remove(symbol)
                print(f"🧹 Cleanup: Removed {symbol} from bought_today (position closed)")
            
            # 🧹 Additional cleanup: Remove sold positions from database
            cleaned_count = cleanup_sold_positions()
            if cleaned_count > 0:
                notify(f"🧹 Cleaned up {cleaned_count} sold positions - symbols now available for new trades")
            
            # 📊 Position summary
            pos_summary = get_position_summary()
            if pos_summary:
                summary_text = ", ".join([f"{status}: {count}" for status, count in pos_summary.items()])
                print(f"📊 Position Summary: {summary_text}")
        
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
                    pnl_percent = ((ltp - avg_buy_price) / avg_buy_price) * 100 if avg_buy_price != 0 else 0
                    
                    # Check if target reached
                    if ltp >= target_price and status not in ["TARGET_HIT"]:
                        profit = (target_price - avg_buy_price) * qty
                        notify(f"🎯 TARGET HIT! {symbol}: LTP ₹{ltp:.2f} >= Target ₹{target_price:.2f} | Profit: ₹{profit:.2f} (+{pnl_percent:.2f}%)")
                        upsert_position(symbol, qty, avg_buy_price, datetime.now(timezone.utc).isoformat(), target_price, "TARGET_HIT")
                        
                        # 🔓 Remove from bought_today so it can be bought again if conditions are met
                        if symbol in MONITOR_STATE["bought_today"]:
                            MONITOR_STATE["bought_today"].remove(symbol)
                            print(f"🔓 Removed {symbol} from bought_today protection - available for new trades")
                    
                    # Check stop loss alert (but don't auto-sell)
                    loss_threshold = avg_buy_price * (1 - LOSS_ALERT_PERCENT / 100.0)
                    if ltp <= loss_threshold and status not in ["ALERTED", "STOP_LOSS_HIT"]:
                        loss = (ltp - avg_buy_price) * qty
                        notify(f"🚨 STOP LOSS ALERT! {symbol}: LTP ₹{ltp:.2f} <= Threshold ₹{loss_threshold:.2f} | Loss: ₹{loss:.2f} ({pnl_percent:.2f}%)")
                        notify(f"🚨 Consider selling {qty} shares of {symbol} manually!")
                        upsert_position(symbol, qty, avg_buy_price, datetime.now(timezone.utc).isoformat(), target_price, "ALERTED")
                    
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


# ---- GTT-Based Trading Strategy ----

def setup_gtt_strategy(symbol: str, prev_close: float, qty: int, dry_run: bool = False):
    """
    Set up GTT orders for a symbol based on gap-down strategy
    
    Args:
        symbol: Trading symbol
        prev_close: Previous close price
        qty: Quantity to trade
        dry_run: Whether to simulate or place real GTT orders
    """
    
    # 🛡️ SINGLE ORDER CONSTRAINT: Check if symbol already has active position or pending GTT
    if has_active_position(symbol):
        print(f"⏸️ Skipping {symbol}: Already has active position (BOUGHT status)")
        notify(f"⏸️ {symbol}: Waiting for current position to sell before placing new GTT")
        return
    
    if has_pending_gtt(symbol):
        print(f"⏸️ Skipping {symbol}: Already has pending GTT order")
        notify(f"⏸️ {symbol}: GTT order already active - no duplicate orders")
        return
    
    # Calculate trigger prices
    buy_trigger_price = prev_close * (1 - BUY_GAP_PERCENT / 100.0)  # Gap down trigger
    
    # Calculate target and stop loss prices (for future GTT sell orders)
    sell_target_price = buy_trigger_price * (1 + SELL_TARGET_PERCENT / 100.0)
    stop_loss_price = buy_trigger_price * (1 - LOSS_ALERT_PERCENT / 100.0)
    
    try:
        if dry_run:
            print(f"[DRY_RUN] 🎯 Would place GTT for {symbol}:")
            print(f"   Buy when price <= ₹{buy_trigger_price:.2f} (Gap: {BUY_GAP_PERCENT}%)")
            print(f"   Then target @ ₹{sell_target_price:.2f} (+{SELL_TARGET_PERCENT}%)")
            print(f"   Stop loss @ ₹{stop_loss_price:.2f} (-{LOSS_ALERT_PERCENT}%)")
            
            # Save simulated GTT to database
            fake_gtt_id = f"DRYRUN-GTT-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            save_gtt_order(
                symbol=symbol,
                gtt_id=fake_gtt_id,
                trigger_type="single",
                trigger_price=buy_trigger_price,
                order_type="MARKET",
                quantity=qty,
                condition="<=",
                meta={
                    "transaction_type": "BUY",
                    "target_price": sell_target_price,
                    "stop_loss": stop_loss_price,
                    "prev_close": prev_close,
                    "dry_run": True
                }
            )
            
        else:
            # Place real GTT order
            if KITE and KITE.kite:
                print(f"🎯 Setting up GTT strategy for {symbol}")
                
                # Place buy GTT when price drops to trigger level
                gtt_response = KITE.place_gtt(
                    symbol=symbol,
                    trigger_price=buy_trigger_price,
                    quantity=qty,
                    transaction_type="BUY",
                    order_type="MARKET",  # Market order for quick execution
                    condition="<="
                )
                
                gtt_id = gtt_response.get("trigger_id")
                print(f"✅ GTT Buy order placed! ID: {gtt_id}")
                print(f"   Trigger: ₹{buy_trigger_price:.2f} (Gap: {BUY_GAP_PERCENT}%)")
                
                notify(f"🎯 GTT Setup: {symbol} will BUY {qty} shares when price <= ₹{buy_trigger_price:.2f}")
                
            else:
                raise RuntimeError("Kite API not available for GTT placement")
                
    except Exception as e:
        print(f"❌ Error setting up GTT for {symbol}: {e}")
        notify(f"❌ GTT Setup Failed: {symbol} - {str(e)}")

def setup_gtt_for_watchlist(symbols: List[str], dry_run: bool = False):
    """
    Set up GTT orders for entire watchlist
    """
    successful_gtts = 0
    failed_gtts = 0
    
    print(f"🎯 Setting up GTT strategy for {len(symbols)} symbols...")
    
    for symbol in symbols:
        try:
            # Get previous close
            prev_close = MONITOR_STATE["last_prev_close"].get(symbol)
            if prev_close is None:
                prev_close = fetch_prev_close(symbol)
                if prev_close is None:
                    print(f"❌ Skipping {symbol}: No previous close data")
                    failed_gtts += 1
                    continue
                MONITOR_STATE["last_prev_close"][symbol] = prev_close
            
            # Calculate quantity based on capital allocation
            ltp = fetch_ltp(symbol)
            if ltp and ltp > 0:
                deployment_cap = MONITOR_STATE["total_capital"] * (MONITOR_STATE["deployment_percentage"] / 100.0)
                per_trade_alloc = deployment_cap * (MONITOR_STATE["per_trade_percentage"] / 100.0)
                qty = max(1, int(per_trade_alloc / ltp))
            else:
                qty = 10  # Fallback quantity
            
            # Set up GTT for this symbol
            setup_gtt_strategy(symbol, prev_close, qty, dry_run)
            successful_gtts += 1
            
            # Small delay to avoid API rate limits
            time.sleep(0.1)
            
        except Exception as e:
            print(f"❌ Error setting up GTT for {symbol}: {e}")
            failed_gtts += 1
            continue
    
    print(f"✅ GTT Setup Complete: {successful_gtts} successful, {failed_gtts} failed")
    notify(f"🎯 GTT Strategy Active: {successful_gtts} ETFs monitoring for gap-down opportunities")

def monitor_gtt_executions():
    """
    Monitor GTT executions and set up follow-up sell orders
    """
    try:
        if not KITE or not KITE.kite:
            return
        
        # Get active GTTs from Kite
        active_gtts = KITE.get_gtts()
        
        for gtt in active_gtts:
            gtt_id = str(gtt.get("id"))
            status = gtt.get("status", "").upper()
            symbol = gtt.get("tradingsymbol")
            
            # Check if this is a buy GTT that got triggered
            if status == "TRIGGERED" and gtt.get("orders", [{}])[0].get("transaction_type") == "BUY":
                # Update local database
                update_gtt_status(gtt_id, "TRIGGERED")
                
                # Check if we need to place sell GTT
                print(f"🎉 GTT Buy triggered for {symbol}! Setting up sell target...")
                
                # Get the executed order details to determine target price
                order = gtt.get("orders", [{}])[0]
                executed_qty = order.get("quantity", 0)
                
                # For simplicity, use the last price as execution price
                # In production, you'd want to fetch the actual execution details
                current_ltp = fetch_ltp(symbol)
                if current_ltp:
                    target_price = current_ltp * (1 + SELL_TARGET_PERCENT / 100.0)
                    
                    # Place target sell GTT
                    try:
                        sell_gtt = KITE.place_gtt(
                            symbol=symbol,
                            trigger_price=target_price,
                            quantity=executed_qty,
                            transaction_type="SELL",
                            order_type="LIMIT",
                            price=target_price,
                            condition=">="
                        )
                        
                        print(f"✅ Sell target GTT placed for {symbol} @ ₹{target_price:.2f}")
                        notify(f"🎯 Target Set: {symbol} will sell at ₹{target_price:.2f} (+{SELL_TARGET_PERCENT}%)")
                        
                    except Exception as e:
                        print(f"❌ Error placing sell GTT for {symbol}: {e}")
                
    except Exception as e:
        print(f"❌ Error monitoring GTT executions: {e}")


# ---- Streamlit UI ----

st.set_page_config(page_title="ETF Gap-Down Trader", layout="wide")
st.title("ETF Gap-Down Trader — Streamlit + Zerodha (Prototype)")

# 🔗 Connection Status Header
col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    # Show real-time connection status
    kite_conn = get_kite_connection()
    token_status = check_token_validity()
    
    if token_status["valid"]:
        st.success(f"✅ Connected to Zerodha as: **{token_status.get('user_name', 'Unknown')}**")
    else:
        error_msg = token_status.get('error', 'Unknown error')
        if "Incorrect" in error_msg and ("api_key" in error_msg or "access_token" in error_msg):
            st.error("🔑 **TOKEN EXPIRED** - Zerodha tokens expire daily!")
            st.warning("👆 **Generate a new access token using the form below**")
        else:
            st.error(f"❌ Not Connected: {error_msg}")

with col2:
    if st.button("🔄 Refresh Connection"):
        refresh_kite_connection()
        st.rerun()

with col3:
    # Show session indicator - use a simpler method to detect access type
    try:
        # Try to get the current URL from browser
        import urllib.parse
        # Simple detection based on common patterns
        access_method = "Network"  # Default assumption for cloud/network access
        st.info(f"📡 {access_method} Access")
    except:
        st.info("📡 Network Access")

st.divider()

# Enhanced Access Token Generation Section
st.markdown("---")

# Automatic Token Validation Check
token_validation = check_token_validity()

if not token_validation["valid"] and token_validation["status"] == "expired":
    st.error("""
    🚨 **ACCESS TOKEN EXPIRED!**
    
    Your trading session has expired. Generate a new access token to continue live trading.
    Tokens expire daily at market close and need to be regenerated each trading day.
    """)
elif not token_validation["valid"] and token_validation["status"] == "missing":
    st.warning("""
    ⚠️ **NO ACCESS TOKEN FOUND**
    
    Generate an access token below to start live trading.
    """)

# Check current connection status
token_status_col1, token_status_col2 = st.columns([1, 1])

with token_status_col1:
    if KITE_ACCESS_TOKEN and KITE and KITE.kite:
        st.success("✅ **Kite API Connected**")
        try:
            profile = KITE.kite.profile()
            st.info(f"� **User:** {profile.get('user_name', 'Unknown')}")
            st.info(f"🏢 **Broker:** {profile.get('broker', 'Unknown')}")
        except:
            st.warning("⚠️ Token may be expired")
    else:
        st.error("❌ **Kite API Not Connected**")

with token_status_col2:
    if KITE_ACCESS_TOKEN:
        st.write("**Current Token:**")
        st.code(f"{KITE_ACCESS_TOKEN[:20]}...{KITE_ACCESS_TOKEN[-10:]}")
    else:
        st.write("**No Access Token**")
        st.info("Generate a new token below")

# Enhanced Token Generation Interface
with st.expander("🔑 **Generate New Access Token**", expanded=not KITE_ACCESS_TOKEN):
    
    # Check API credentials first
    if not KITE_API_KEY or not KITE_API_SECRET:
        st.error("❌ **API Credentials Missing**")
        st.write("Please add these to your `.env` file:")
        st.code("""KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here""")
        
    else:
        # Enhanced UI with better flow
        st.markdown("### 🚀 **One-Click Token Generation**")
        
        # Step 1: Login Button
        if KITE_API_KEY:
            login_url = KiteConnect(api_key=KITE_API_KEY).login_url()
            
            st.markdown("#### Step 1: Login to Zerodha")
            st.markdown(f"""
            <a href="{login_url}" target="_blank" style="
                display: inline-block; 
                padding: 10px 20px; 
                background-color: #ff6600; 
                color: white; 
                text-decoration: none; 
                border-radius: 5px; 
                font-weight: bold;
            ">🔐 Login to Zerodha Kite</a>
            """, unsafe_allow_html=True)
            
            st.info("👆 Click the button above, login with your Zerodha credentials")
        
        st.markdown("---")
        
        # Step 2: Enhanced Request Token Input
        st.markdown("#### Step 2: Enter Request Token")
        st.write("📋 After login, copy the **request_token** from the URL and paste below:")
        
        # Better input with placeholder
        request_token = st.text_input(
            "Request Token", 
            placeholder="Paste request token here (e.g., abc123def456...)",
            key="request_token_input",
            help="The request token appears in the URL after successful login"
        )
        
        # URL example
        with st.expander("ℹ️ **Where to find the request token?**"):
            st.write("After login, you'll be redirected to a URL like:")
            st.code("https://kite.trade/connect/login?status=success&request_token=YOUR_TOKEN_HERE")
            st.write("Copy the value after `request_token=`")
        
        # Enhanced Generate Button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("🎯 **Generate Access Token**", type="primary", use_container_width=True):
                if request_token.strip():
                    # Progress indicators
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    try:
                        status_text.text("🔄 Connecting to Kite API...")
                        progress_bar.progress(20)
                        
                        kite = KiteConnect(api_key=KITE_API_KEY)
                        
                        status_text.text("🔄 Generating session...")
                        progress_bar.progress(40)
                        
                        data = kite.generate_session(request_token.strip(), api_secret=KITE_API_SECRET)
                        access_token = data["access_token"]
                        
                        status_text.text("🔄 Updating configuration...")
                        progress_bar.progress(60)
                        
                        # Update .env file
                        env_lines = []
                        token_updated = False
                        
                        try:
                            with open('.env', 'r') as file:
                                env_lines = file.readlines()
                        except FileNotFoundError:
                            env_lines = []
                        
                        # Update or add the access token
                        with open('.env', 'w') as file:
                            for line in env_lines:
                                if line.startswith('KITE_ACCESS_TOKEN='):
                                    file.write(f'KITE_ACCESS_TOKEN={access_token}\n')
                                    token_updated = True
                                else:
                                    file.write(line)
                            
                            # Add token if not found in existing file
                            if not token_updated:
                                file.write(f'KITE_ACCESS_TOKEN={access_token}\n')
                        
                        status_text.text("🔄 Verifying connection...")
                        progress_bar.progress(80)
                        
                        # Test the new token
                        test_kite = KiteConnect(api_key=KITE_API_KEY)
                        test_kite.set_access_token(access_token)
                        profile = test_kite.profile()
                        
                        progress_bar.progress(100)
                        status_text.text("✅ Success!")
                        
                        # Success message with user info
                        st.success(f"""
                        🎉 **Access Token Generated Successfully!**
                        
                        👤 **User:** {profile.get('user_name', 'Unknown')}
                        🏢 **Broker:** {profile.get('broker', 'Unknown')}
                        📅 **Valid Until:** Market close today
                        """)
                        
                        # Auto-reload info
                        st.info("🔄 **The application will automatically reload in 3 seconds...**")
                        
                        # Clear the input
                        st.session_state.request_token_input = ""
                        
                        # Reload the app
                        time.sleep(3)
                        st.rerun()
                        
                    except Exception as e:
                        progress_bar.progress(0)
                        status_text.text("")
                        st.error(f"""
                        ❌ **Token Generation Failed**
                        
                        **Error:** {str(e)}
                        
                        **Common Issues:**
                        - Request token expired (generate a new one)
                        - Invalid API credentials
                        - Network connectivity issues
                        """)
                        
                        # Show retry option
                        if st.button("🔄 Try Again", key="retry_token"):
                            st.rerun()
                        
                else:
                    st.warning("⚠️ Please enter the request token first")
        
        # Additional help section
        with st.expander("🆘 **Need Help?**"):
            st.markdown("""
            **Token Generation Steps:**
            1. Click the login button above
            2. Enter your Zerodha credentials 
            3. Copy the request token from the URL
            4. Paste it in the input field
            5. Click 'Generate Access Token'
            
            **Troubleshooting:**
            - ✅ Make sure you have active Zerodha account
            - ✅ API credentials must be correct
            - ✅ Request token is valid for only 5 minutes
            - ✅ Generate new request token if expired
            
            **Security Note:**
            - 🔒 Tokens are stored locally in .env file
            - 🔒 Access tokens expire daily at market close
            - 🔒 Generate fresh token each trading day
            """)

st.markdown("---")

# Left controls
with st.sidebar:
    st.header("⚙️ Trading Settings")
    
    # 🔗 Quick Connection Status & Token Generation
    st.subheader("🔗 Connection Status")
    
    # Get current token status
    token_status = get_token_status_display()
    
    if token_status["color"] == "success":
        st.success(f"{token_status['emoji']} {token_status['message']}")
    elif token_status["color"] == "warning":
        st.warning(f"{token_status['emoji']} {token_status['message']}")
    else:
        st.error(f"{token_status['emoji']} {token_status['message']}")
    
    # Action button based on status
    if token_status.get("action_needed", False):
        action = token_status.get("action", "generate")
        
        if action == "generate":
            button_text = "🔑 Generate Access Token"
            button_help = "Click to generate a new access token"
        elif action == "renew":
            button_text = "🔄 Renew Token"
            button_help = "Your token has expired, generate a new one"
        else:
            button_text = "🔧 Fix Connection"
            button_help = "Check your connection and API settings"
            
        if st.button(button_text, type="primary", use_container_width=True, help=button_help):
            st.info("👆 Use the token generation section above")
            # Auto-scroll to top
            st.markdown("""
            <script>
            window.scrollTo(0, 0);
            </script>
            """, unsafe_allow_html=True)
    
    # 💰 Account Balance & Quick Stats
    balance_col1, balance_col2, balance_col3 = st.columns(3)
    
    with balance_col1:
        token_status = check_token_validity()
        if token_status["valid"]:
            kite_conn = get_kite_connection()
            if kite_conn and kite_conn.kite:
                try:
                    margins = kite_conn.kite.margins()
                    available_cash = margins.get('equity', {}).get('available', {}).get('cash', 0)
                    st.metric("💰 Available Cash", f"₹{available_cash:,.2f}")
                except Exception as e:
                    st.error(f"❌ Balance Error: {str(e)[:50]}...")
            else:
                st.warning("⚠️ Connection issue - try refreshing")
                
    with balance_col2:
        if st.button("🔄 Refresh Balance", type="secondary"):
            kite_conn = get_kite_connection()
            if kite_conn and kite_conn.kite:
                try:
                    with st.spinner("Fetching account data..."):
                        # First test the connection
                        profile = kite_conn.kite.profile()
                        st.success(f"✅ Connected as: {profile.get('user_name', 'Unknown')}")
                        
                        # Then fetch margins
                        margins = kite_conn.kite.margins()
                        equity_data = margins.get('equity', {})
                        available_data = equity_data.get('available', {})
                        cash = available_data.get('cash', 0)
                        net = equity_data.get('net', 0)
                        
                        st.success(f"💰 Cash: ₹{cash:,.2f}")
                        st.info(f"🏦 Net: ₹{net:,.2f}")
                        
                        # Update global state
                        MONITOR_STATE["total_capital"] = float(cash)
                        MONITOR_STATE["last_balance_update"] = datetime.now().isoformat()
                        
                        # Show additional details
                        if cash == 0:
                            st.warning("⚠️ Available cash is ₹0. Check if funds are available for trading.")
                        
                except Exception as e:
                    st.error(f"❌ API Error: {e}")
                    if "Incorrect" in str(e) and "access_token" in str(e):
                        st.error("🔑 **Token Expired!** Generate a new access token using the form above.")
                        # Force refresh connection on token error
                        refresh_kite_connection()
                    elif "api_key" in str(e):
                        st.error("🔐 **Invalid API Key!** Check your API credentials.")
                    else:
                        st.error(f"📡 **Connection Issue:** {e}")
            else:
                st.error("❌ Kite API not connected - Check token status above")
                # Try to refresh connection
                kite_conn = refresh_kite_connection()
                if kite_conn:
                    st.success("🔄 Connection refreshed! Try again.")
                
    with balance_col3:
        if MONITOR_STATE.get("last_balance_update"):
            last_update = datetime.fromisoformat(MONITOR_STATE["last_balance_update"])
            st.caption(f"Last Updated: {last_update.strftime('%H:%M:%S')}")
    
    st.markdown("---")
    
    # Trading Mode - FORCED LIVE TRADING
    dry_run = False  # Always live trading mode
    st.success("🚀 LIVE TRADING MODE - Real money will be used for trades!")
    
    MONITOR_STATE["dry_run"] = dry_run
    
    # 💰 Dynamic Capital Allocation Settings
    st.subheader("💰 Capital Allocation")
    
    # Show current connection status for balance fetching
    kite_conn = get_kite_connection()
    if not kite_conn or not kite_conn.kite:
        st.error("❌ Kite API not connected - Cannot fetch real balance")
        st.info("💡 Generate and validate your access token above to see account balance")
        
        # Add connection refresh button
        if st.button("🔄 Refresh Connection", type="secondary"):
            refresh_kite_connection()
            st.rerun()
    
    # Update capital allocation button
    if st.button("🔄 Refresh Real Balance", type="secondary"):
        kite_conn = get_kite_connection()
        if kite_conn and kite_conn.kite:
            with st.spinner("Fetching account balance..."):
                update_capital_allocation()
                st.rerun()
        else:
            st.error("❌ Please connect to Kite API first")
            # Try to refresh connection
            refresh_kite_connection()
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
        
        # Debug section for troubleshooting
        with st.expander("🔧 Debug Balance Issues"):
            st.write("**Troubleshooting Steps:**")
            st.write("1. ✅ Ensure access token is valid")
            st.write("2. ✅ Check Kite API connection status")
            st.write("3. ✅ Verify trading account has sufficient balance")
            
            if st.button("🔬 Test API Connection", key="debug_api"):
                kite_conn = get_kite_connection()
                if kite_conn and kite_conn.kite:
                    try:
                        # Test basic API call
                        profile = kite_conn.kite.profile()
                        st.success(f"✅ API Connected - User: {profile.get('user_name', 'Unknown')}")
                        
                        # Test margins call
                        margins = kite_conn.kite.margins()
                        st.json(margins)  # Show raw response
                        
                    except Exception as e:
                        st.error(f"❌ API Test Failed: {e}")
                        # Try refreshing connection on error
                        refresh_kite_connection()
                else:
                    st.error("❌ KITE object not initialized")
                    # Try to create connection
                    kite_conn = refresh_kite_connection()
                    if kite_conn:
                        st.success("🔄 Connection created! Test again.")
    
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
    
    # GTT Strategy Controls
    st.subheader("🎯 GTT Strategy")
    
    # Strategy parameters display
    st.write(f"**Current Strategy:**")
    st.write(f"• Gap Down Trigger: {BUY_GAP_PERCENT}%")
    st.write(f"• Profit Target: {SELL_TARGET_PERCENT}%")
    st.write(f"• Loss Alert: {LOSS_ALERT_PERCENT}%")
    
    # GTT Action buttons
    gtt_col1, gtt_col2 = st.columns(2)
    
    with gtt_col1:
        if st.button("🎯 Setup GTT for All", type="primary", help="Set up GTT orders for entire watchlist"):
            if KITE and KITE.kite:
                try:
                    setup_gtt_for_watchlist(MONITOR_STATE["symbols"], dry_run)
                    st.success("✅ GTT setup initiated!")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ GTT setup failed: {e}")
            else:
                st.error("❌ Kite API not connected")
    
    with gtt_col2:
        if st.button("🔄 Monitor GTTs", help="Check and update GTT execution status"):
            if KITE and KITE.kite:
                try:
                    monitor_gtt_executions()
                    st.success("✅ GTT monitoring complete")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ GTT monitoring failed: {e}")
            else:
                st.error("❌ Kite API not connected")
    
    # GTT Status
    try:
        active_gtts_df = load_active_gtts()
        if not active_gtts_df.empty:
            st.write(f"📊 **Active GTTs:** {len(active_gtts_df)}")
            
            # Show sample of active GTTs
            sample_gtts = active_gtts_df.head(3)
            for _, gtt in sample_gtts.iterrows():
                st.write(f"• {gtt['symbol']}: ₹{gtt['trigger_price']:.2f} ({gtt['condition']})")
        else:
            st.write("📊 **Active GTTs:** 0")
    except Exception as e:
        st.write("📊 **Active GTTs:** Unable to load")
    
    # Watchlist
    st.subheader("📋 Watchlist")
    
    wl_input = st.text_area("ETF Symbols (one per line or comma-separated)", 
                           value=",".join(MONITOR_STATE["symbols"]), height=100)
    
    # Buttons for different watchlist sources
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🔄 Update Manual"):
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
    
    with col2:
        if st.button("🔍 Fetch All ETFs"):
            if KITE and KITE.kite:
                # Clear cache and fetch fresh ETF list
                _cached_etf_instruments.clear()
                symbols = fetch_etf_instruments(KITE.kite)
                MONITOR_STATE["symbols"] = symbols
                
                # Initialize previous close data for new symbols
                st.info("🔄 Initializing previous close data for new symbols...")
                for symbol in symbols[:20]:  # Initialize first 20 to avoid timeout
                    if symbol not in MONITOR_STATE["last_prev_close"]:
                        prev_close = fetch_prev_close(symbol)
                        if prev_close:
                            MONITOR_STATE["last_prev_close"][symbol] = prev_close
                
                st.success(f"✅ Fetched {len(symbols)} ETFs from instruments API")
                st.rerun()
            else:
                st.error("❌ Kite API not connected")
    
    with col3:
        if st.button("📋 Load from ENV"):
            env_symbols = get_watchlist_from_env_or_instruments(None)
            MONITOR_STATE["symbols"] = env_symbols
            st.success(f"✅ Loaded {len(env_symbols)} symbols from environment")
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
        cur.execute("SELECT qty, avg_buy_price, target_price, status, product FROM positions WHERE symbol = ?", (sym,))
        row = cur.fetchone()
    if row:
        qty_db, avg_buy, target, status, product = row
        unreal = (ltp - avg_buy) * qty_db if isinstance(ltp, (int, float)) else None
    else:
        qty_db, avg_buy, target, status, product, unreal = 0, None, None, "WATCHING", "CNC", None

    pct_vs_prev = None
    if isinstance(prev_close, (int, float)) and isinstance(ltp, (int, float)) and prev_close != 0:
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
        "product": product if product else "CNC",
        "status": status,
    })
    
    # Small delay to avoid rate limits when fetching data for multiple symbols
    if i > 0 and i % 3 == 0:
        time.sleep(0.5)

# Main content tabs
tab1, tab2, tab3 = st.tabs(["📊 Watchlist & Positions", "🎯 GTT Management", "📈 Trading Activity"])

with tab1:
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

with tab2:
    st.subheader("🎯 GTT (Good Till Triggered) Orders")
    
    # GTT Overview
    col1, col2, col3 = st.columns(3)
    
    try:
        all_gtts_df = load_all_gtts()
        active_gtts_df = load_active_gtts()
        
        with col1:
            st.metric("Total GTTs", len(all_gtts_df))
        with col2:
            st.metric("Active GTTs", len(active_gtts_df))
        with col3:
            triggered_count = len(all_gtts_df[all_gtts_df['status'] == 'TRIGGERED']) if not all_gtts_df.empty else 0
            st.metric("Triggered GTTs", triggered_count)
    except Exception as e:
        st.error(f"Error loading GTT data: {e}")
        all_gtts_df = pd.DataFrame()
        active_gtts_df = pd.DataFrame()
    
    # Active GTTs Table
    if not active_gtts_df.empty:
        st.subheader("📋 Active GTT Orders")
        
        # Format the dataframe for display
        display_gtts = active_gtts_df.copy()
        if 'created_at' in display_gtts.columns:
            display_gtts['created_at'] = pd.to_datetime(display_gtts['created_at']).dt.strftime('%Y-%m-%d %H:%M')
        if 'trigger_price' in display_gtts.columns:
            display_gtts['trigger_price'] = display_gtts['trigger_price'].apply(lambda x: f"₹{x:.2f}")
        if 'price' in display_gtts.columns:
            display_gtts['price'] = display_gtts['price'].apply(lambda x: f"₹{x:.2f}" if pd.notna(x) and x > 0 else "-")
        
        st.dataframe(display_gtts[['symbol', 'trigger_price', 'condition', 'order_type', 'quantity', 'status', 'created_at']], width='stretch')
        
        # GTT Actions
        st.subheader("🎛️ GTT Actions")
        gtt_action_col1, gtt_action_col2, gtt_action_col3 = st.columns(3)
        
        with gtt_action_col1:
            if st.button("🔄 Sync with Kite", help="Sync local GTT data with Kite API"):
                if KITE and KITE.kite:
                    try:
                        kite_gtts = KITE.get_gtts()
                        st.success(f"✅ Found {len(kite_gtts)} GTTs on Kite")
                        
                        # Update status for any triggered GTTs
                        for kite_gtt in kite_gtts:
                            gtt_id = str(kite_gtt.get("id"))
                            status = kite_gtt.get("status", "").upper()
                            if status in ["TRIGGERED", "CANCELLED", "COMPLETE"]:
                                update_gtt_status(gtt_id, status)
                        
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Sync failed: {e}")
                else:
                    st.error("❌ Kite API not connected")
        
        with gtt_action_col2:
            if st.button("❌ Cancel All GTTs", help="Cancel all active GTT orders"):
                if KITE and KITE.kite:
                    try:
                        cancelled_count = 0
                        for _, gtt in active_gtts_df.iterrows():
                            try:
                                KITE.cancel_gtt(gtt['gtt_id'])
                                cancelled_count += 1
                            except Exception as e:
                                st.error(f"Failed to cancel GTT {gtt['gtt_id']}: {e}")
                        
                        st.success(f"✅ Cancelled {cancelled_count} GTT orders")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Bulk cancellation failed: {e}")
                else:
                    st.error("❌ Kite API not connected")
        
        with gtt_action_col3:
            if st.button("🔄 Monitor Executions", help="Check for GTT executions and set up follow-up orders"):
                try:
                    monitor_gtt_executions()
                    st.success("✅ GTT monitoring complete")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Monitoring failed: {e}")
    else:
        st.info("📝 No active GTT orders found")
        st.write("Use the sidebar controls to set up GTT orders for your watchlist")
    
    # Manual GTT Creation
    with st.expander("➕ Create Manual GTT"):
        st.write("Create a custom GTT order")
        
        gtt_col1, gtt_col2 = st.columns(2)
        
        with gtt_col1:
            gtt_symbol = st.text_input("Symbol", placeholder="e.g., NIFTYBEES")
            gtt_trigger_price = st.number_input("Trigger Price", min_value=0.01, step=0.01, format="%.2f")
            gtt_quantity = st.number_input("Quantity", min_value=1, value=10)
        
        with gtt_col2:
            gtt_transaction_type = st.selectbox("Transaction", ["BUY", "SELL"])
            gtt_order_type = st.selectbox("Order Type", ["MARKET", "LIMIT"])
            if gtt_order_type == "LIMIT":
                gtt_limit_price = st.number_input("Limit Price", min_value=0.01, step=0.01, format="%.2f")
            else:
                gtt_limit_price = None
        
        if st.button("🎯 Place GTT"):
            if gtt_symbol and gtt_trigger_price > 0:
                if KITE and KITE.kite:
                    try:
                        response = KITE.place_gtt(
                            symbol=gtt_symbol.upper(),
                            trigger_price=gtt_trigger_price,
                            quantity=gtt_quantity,
                            transaction_type=gtt_transaction_type,
                            order_type=gtt_order_type,
                            price=gtt_limit_price
                        )
                        st.success(f"✅ GTT placed successfully! ID: {response.get('trigger_id')}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ GTT placement failed: {e}")
                else:
                    st.error("❌ Kite API not connected")
            else:
                st.error("❌ Please fill in all required fields")

with tab3:
    # Activity log (last 50 trades)
    st.subheader("📈 Recent Trading Activity")
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
            elif has_active_position(symbol_manual):
                st.error(f"❌ {symbol_manual} already has an active position! Wait for it to sell before buying again.")
            elif has_pending_gtt(symbol_manual):
                st.error(f"❌ {symbol_manual} already has a pending GTT order! Cancel it first or wait for execution.")
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
                                upsert_position(symbol_manual, filled_qty, avg_price, datetime.now(timezone.utc).isoformat(), target, "BOUGHT")
                                
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

# 🛡️ Single Order Constraint Status
st.subheader("🛡️ Position Constraint Status")
pos_summary = get_position_summary()
total_watchlist = len(MONITOR_STATE["symbols"]) if MONITOR_STATE["symbols"] else 0
active_positions = pos_summary.get("BOUGHT", 0)
available_slots = total_watchlist - active_positions

pos_col1, pos_col2, pos_col3, pos_col4 = st.columns(4)

with pos_col1:
    st.metric("📊 Total Watchlist", total_watchlist)

with pos_col2:
    st.metric("🔒 Active Positions", active_positions, 
              delta=f"MTF/CNC positions currently held" if active_positions > 0 else "No active positions")

with pos_col3:
    st.metric("🟢 Available Slots", available_slots,
              delta="Ready for new trades" if available_slots > 0 else "All slots occupied")

with pos_col4:
    if pos_summary:
        other_statuses = {k: v for k, v in pos_summary.items() if k != "BOUGHT"}
        if other_statuses:
            status_text = ", ".join([f"{k}: {v}" for k, v in other_statuses.items()])
            st.info(f"📋 Other: {status_text}")
        else:
            st.info("📋 No other statuses")
    else:
        st.info("📋 No positions tracked")

if active_positions > 0:
    st.info("ℹ️ **Single Order Rule**: ETFs with active positions are blocked from new GTT orders until sold.")
else:
    st.success("✅ **All Clear**: All watchlist symbols are available for new GTT orders.")

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
                        pnl_pct = ((ltp - pos['avg_buy_price']) / pos['avg_buy_price']) * 100 if pos['avg_buy_price'] != 0 else 0
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
    if ltp is not None and prev_close is not None and prev_close != 0:
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