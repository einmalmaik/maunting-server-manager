FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    MSM_WETTER_DATEN=/daten \
    TZ=UTC

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<0.116" \
    "uvicorn[standard]>=0.32,<0.33" \
    "httpx>=0.27,<0.28" \
    "eccodes>=2.38,<2.39" \
    "numpy>=2.1,<2.2"

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
