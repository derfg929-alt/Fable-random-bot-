FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives on a volume so data survives container rebuilds.
RUN mkdir -p /data
ENV DATABASE_URL=sqlite+aiosqlite:////data/fable_randomizer.db

EXPOSE 8000
CMD ["python", "main.py"]
