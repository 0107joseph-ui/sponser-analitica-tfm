# Imagen del backend + pipeline de datos (sponser_api + sponser_etl).
#
# Ambos requirements.txt se instalan en el MISMO intérprete: pipeline_runner.py
# dispara sponser_etl como subproceso con sys.executable (el mismo Python que
# corre la API), igual que en desarrollo local (ver README, "Instalación y
# arranque"). sponser_etl se copia como hermano de sponser_api dentro de la
# imagen porque config.py resuelve su ruta como ../sponser_etl relativo a sí
# mismo -- si no fueran hermanos, el backend no encontraría los datos/modelo.
FROM python:3.11-slim

# libgomp1: LightGBM lo necesita en tiempo de ejecución (OpenMP) y no viene
# instalado en la imagen slim de Debian -- sin esto, "import lightgbm" falla
# con "libgomp.so.1: cannot open shared object file".
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY sponser_api/requirements.txt sponser_api/requirements.txt
COPY sponser_etl/requirements.txt sponser_etl/requirements.txt
RUN pip install --no-cache-dir \
      -r sponser_api/requirements.txt \
      -r sponser_etl/requirements.txt

COPY sponser_api sponser_api
COPY sponser_etl sponser_etl

WORKDIR /app/sponser_api

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

# --host 0.0.0.0 (no 127.0.0.1 como en el README de desarrollo local): dentro
# de un contenedor, bind a loopback no es alcanzable desde el mapeo de puertos
# del host.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
