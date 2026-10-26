FROM python:3.13-slim-trixie@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39
ARG CALLRELAY_UID=1000
ARG CALLRELAY_GID=1000
ADD --checksum=sha256:d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1 https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip /tmp/platform-tools.zip
RUN python -c "import zipfile; z=zipfile.ZipFile('/tmp/platform-tools.zip'); open('/usr/local/bin/adb','wb').write(z.read('platform-tools/adb'))" \
    && chmod 0755 /usr/local/bin/adb \
    && rm /tmp/platform-tools.zip \
    && (getent group "$CALLRELAY_GID" || groupadd --gid "$CALLRELAY_GID" phone) \
    && useradd --uid "$CALLRELAY_UID" --gid "$CALLRELAY_GID" --create-home --shell /usr/sbin/nologin phone
WORKDIR /app
COPY controller.py healthcheck.py /app/
USER phone
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "/app/controller.py"]
