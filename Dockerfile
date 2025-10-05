FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application code
COPY . .

# Expose Streamlit port
EXPOSE 8501

# Create directory for database
RUN mkdir -p /app/data

# Set environment variable for database location
ENV DB_FILE=/app/data/trades.db

# Run Streamlit
CMD ["streamlit", "run", "streamlit_kite_etf_trader.py", "--server.port=8501", "--server.address=0.0.0.0"]