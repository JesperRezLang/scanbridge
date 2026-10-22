# Scan button on Brother printers -> files. Python standard library only.
FROM python:3.14.5-alpine3.22

WORKDIR /app
COPY scanbridge ./scanbridge

RUN adduser -D -H -u 10001 scanbridge && mkdir -p /data && chown scanbridge /data
USER scanbridge

ENV SCANBRIDGE_DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
VOLUME /data
# The printer always sends the button press here.
EXPOSE 54925/udp

ENTRYPOINT ["python", "-m", "scanbridge"]
