FROM python:3.11-slim

# Install system dependencies for OCR and PaddleOCR
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Render will set $PORT)
EXPOSE 5000

# Run gunicorn with OCR-optimized config
# Config file handles PORT environment variable
CMD ["gunicorn", "--config", "gunicorn_config.py", "app:app"]
