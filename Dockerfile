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
#
# v5.88.73: Removed `--no-cache-dir`. That flag was making pip
# re-download every wheel from PyPI on every cache-miss build,
# including torch (~700MB) pulled in by sentence-transformers and
# the rest of the ML stack. The image gets a few hundred MB larger,
# but each build re-uses cached wheels from the local pip cache and
# avoids the network round-trip. Render's deploy time was the
# constraint, not image size.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application code
COPY . .

# Expose port (Render will set $PORT)
EXPOSE 5000

# Startup: run Alembic migrations first, then launch gunicorn.
# Bootstrap handles the case where production DB has tables but no
# alembic_version (pre-v5.86.71 state) by stamping at the base revision
# before upgrading. Script is idempotent and safe on every deploy.
#
# Using sh -c so we can chain commands. If bootstrap fails it exits 0
# (intentional — let the app start and surface problems via diagnostics
# rather than failing the container). If gunicorn fails, that's a real
# deploy failure and Render will retry.
CMD ["sh", "-c", "python scripts/bootstrap_alembic.py && gunicorn --config gunicorn_config.py app:app"]
