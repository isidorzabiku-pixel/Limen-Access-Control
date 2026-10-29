# Limen master (Linux). Uso: vedi docs/DEVELOPMENT.md -> «Docker».
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 limen \
 && mkdir /data && chown limen /data

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
RUN chmod +x avvia.sh

# Database, certificati e chiave delle sessioni stanno nel volume /data (sopravvivono agli aggiornamenti).
ENV LIMEN_DATA=/data PYTHON=python3 PORTA=8443 TIMEZONE=Europe/Rome PYTHONUNBUFFERED=1
VOLUME /data
USER limen
EXPOSE 8443/tcp 57990/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python3 -c "import ssl,urllib.request,os;c=ssl._create_unverified_context();urllib.request.urlopen('https://127.0.0.1:%s/login'%os.environ.get('PORTA','8443'),context=c,timeout=4)"

CMD ["./avvia.sh"]
