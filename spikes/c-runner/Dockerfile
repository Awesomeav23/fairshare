FROM python:3.13-slim
RUN pip install --no-cache-dir h2o==3.46.0.12 boto3 \
 && rm -f /usr/local/lib/python3.13/site-packages/h2o/backend/bin/h2o.jar   # client never needs the server jar
RUN useradd --uid 10001 runner
USER 10001
COPY runner.py /app/runner.py
ENTRYPOINT ["python", "-u", "/app/runner.py"]
