FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY Documentação/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser Projeto/src/api /app/src/api
COPY --chown=appuser:appuser Projeto/src/comunicacao /app/src/comunicacao
COPY --chown=appuser:appuser Projeto/src/config /app/src/config
COPY --chown=appuser:appuser Projeto/src/fuzzy_system/*.py /app/src/fuzzy_system/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "api.app:app", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
