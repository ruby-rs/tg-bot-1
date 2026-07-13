FROM python:3.12-slim

WORKDIR /app

# Install both bot and web dependencies so a single image can run either service.
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

COPY bot ./bot
COPY webapp ./webapp

# Data (SQLite db) lives on a mounted volume.
ENV DB_PATH=/data/life_tracker.db
VOLUME ["/data"]

EXPOSE 8000

# Default command runs the web app; the bot service overrides it in compose.
CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8000"]
