FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home emulator

USER emulator
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=2s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/_emulator/health')"

CMD ["asaas-emulator", "--host", "0.0.0.0", "--port", "8000"]
