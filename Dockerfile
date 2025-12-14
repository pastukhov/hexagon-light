FROM python:3.11-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends bluez dbus \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m pip install --no-cache-dir bleak

COPY hexagon_light.py /app/hexagon_light.py
COPY README.md /app/README.md

ENTRYPOINT ["python3", "/app/hexagon_light.py"]
