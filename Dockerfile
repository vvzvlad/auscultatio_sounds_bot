FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create data directory for working files
RUN mkdir -p data

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire audio directory
COPY audio audio/

# Copy the application code
COPY bot.py question_manager.py questions.json ./

CMD ["python", "bot.py"] 