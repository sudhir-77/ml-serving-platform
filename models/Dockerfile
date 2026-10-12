FROM python:3.12-slim

WORKDIR /app
COPY models/requirements-serve.txt /tmp/requirements-serve.txt
RUN pip install --no-cache-dir -r /tmp/requirements-serve.txt
COPY src /app/src
COPY models/server.py /app/server.py
ENV PYTHONPATH=/app/src
ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "server.py"]
