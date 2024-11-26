FROM python:3.11-slim

WORKDIR /app


# Create data directory for working files
RUN mkdir -p data

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY questions/ questions/
COPY bot.py .

# Run the application
CMD ["python", "bot.py"]