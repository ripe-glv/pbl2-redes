FROM python:3.12-slim

WORKDIR /app

COPY src/ /app/
COPY dashboard/ /app/dashboard/

ENV PYTHONUNBUFFERED=1

CMD ["python", "broker.py"]
