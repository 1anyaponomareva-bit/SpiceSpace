# Railway: репозиторий = SpiceSpace 2, код бота = spicespace-bot/
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY spicespace-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spicespace-bot/ .

EXPOSE 8080

CMD ["python", "main.py"]
