FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN adduser --disabled-password --gecos '' appuser
USER appuser

COPY gtm_triage/ ./gtm_triage/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/ready', timeout=4).raise_for_status()"

CMD ["uvicorn", "gtm_triage.api:app", "--host", "0.0.0.0", "--port", "8000"]
