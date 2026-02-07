FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY moneyclaw/ moneyclaw/
COPY strategies/ strategies/

# Data volume
VOLUME /app/data

EXPOSE 8080

CMD ["moneyclaw", "run"]
