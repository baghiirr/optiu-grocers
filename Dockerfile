FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY clover_connector/ ./clover_connector/
COPY opti_connector/ ./opti_connector/

RUN pip install --no-cache-dir .

COPY deploy/entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
