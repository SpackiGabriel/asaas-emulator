# syntax=docker/dockerfile:1

# Build stage: produce a wheel, so the runtime image carries no build tooling.
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels .

# Runtime stage.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install /wheels/*.whl \
 && useradd --create-home --uid 10001 emulator \
 && mkdir /data && chown emulator /data

USER emulator
WORKDIR /home/emulator
# Mount a volume here and set ASAAS_EMULATOR_DATABASE=/data/asaas.db to keep state.
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=2s --start-period=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/_emulator/health')"]

ENTRYPOINT ["asaas-emulator"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
