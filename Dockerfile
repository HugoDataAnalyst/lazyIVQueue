FROM python:3.12-slim-bookworm

WORKDIR /app

# Install the build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev libssl-dev libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application code
COPY . .

# Command to run the application
CMD ["python", "-m", "LazyIVQueue.lazyivqueue"]
