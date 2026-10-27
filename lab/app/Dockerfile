FROM python:3.12-slim

RUN useradd -u 1000 -m appuser \
  && mkdir -p /app /data \
  && chown 1000:1000 /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER 1000:1000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
