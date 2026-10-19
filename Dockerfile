FROM python:3.12-slim
WORKDIR /app
COPY app.py monitoring.py notifications.py holders.py holder_history_sources.py /app/
COPY static /app/static
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080 DATA_DIR=/data
RUN mkdir -p /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=3)"
CMD ["python", "app.py"]
