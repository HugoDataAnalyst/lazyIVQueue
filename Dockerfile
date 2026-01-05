FROM python:3.12-slim-buster

WORKDIR /LazyIVQueue

# Install the build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev libssl-dev libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY . .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Command to run the application
CMD ["python", "lazyivqueue.py"]
