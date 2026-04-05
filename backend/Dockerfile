FROM python:3.12-slim-bookworm

# System deps required by Playwright/Chromium on Linux
# + Xvfb / x11vnc / noVNC / websockify for the headful auth-browser feature
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libdrm2 libgbm1 libxcomposite1 libxdamage1 \
    libxrandr2 libpangocairo-1.0-0 libcairo-gobject2 \
    libgtk-3-0 libx11-xcb1 libxcb-dri3-0 \
    xvfb x11vnc novnc python3-websockify \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure /app (and its packages: app/, runner/) are always on the Python path
ENV PYTHONPATH=/app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium \
 && python -m playwright install-deps chromium

# Copy application code
COPY . .

# Create storage directories (bind-mounted volumes override in docker-compose)
RUN mkdir -p /app/uploads /app/results

EXPOSE 8000
