# RPA Vibe Studio: оркестратор (веб-интерфейс + API) и робот для Linux.
#   docker build -t rpa-vibe-studio .                    — оркестратор
#   docker build -t rpa-vibe-robot --target robot .      — робот (Chromium, Tesseract)
FROM node:20-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt "psycopg[binary]>=3.1"
COPY rpa_engine ./rpa_engine
COPY rpa_robot ./rpa_robot
COPY orchestrator ./orchestrator
COPY templates ./templates
COPY examples ./examples

FROM base AS robot
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/* \
    && pip install pytesseract pillow opencv-python-headless numpy pypdfium2 \
    && playwright install --with-deps chromium
ENTRYPOINT ["python", "-m", "rpa_robot", "agent"]

FROM base AS orchestrator
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/* && pip install pytesseract pillow pypdfium2
COPY --from=frontend /src/frontend/dist ./frontend/dist
VOLUME ["/data"]
ENV RPA_DATABASE_URL=sqlite:////data/rpa.db RPA_SECRET_KEY_FILE=/data/rpa_secret.key
EXPOSE 8000
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
