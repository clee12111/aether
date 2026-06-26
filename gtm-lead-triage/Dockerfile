FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn pydantic openai httpx langfuse "psycopg[binary]"

COPY gtm_triage/ ./gtm_triage/

EXPOSE 8000

CMD ["uvicorn", "gtm_triage.api:app", "--host", "0.0.0.0", "--port", "8000"]
