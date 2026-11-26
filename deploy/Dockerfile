FROM python:3.12-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr=5.5.0-1+b1 tesseract-ocr-eng=1:4.1.0-2 tesseract-ocr-ara=1:4.1.0-2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY requirements-server.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-server.lock
COPY agent ./agent
COPY server ./server
COPY Contracts ./Contracts
RUN pip install --no-cache-dir --no-deps .
EXPOSE 48793
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:48793/health',timeout=3)"
CMD ["python", "-m", "server.app"]
