# Railway (ветка master): клонируем актуальный бот с main — submodule при COPY часто пустой
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

ARG BOT_REPO=https://github.com/1anyaponomareva-bit/SpiceSpace.git
ARG BOT_BRANCH=main

RUN git clone --depth 1 --branch "${BOT_BRANCH}" "${BOT_REPO}" /tmp/bot \
    && cp -a /tmp/bot/. /app/ \
    && rm -rf /tmp/bot

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["python", "main.py"]
