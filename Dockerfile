FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Europe/Stockholm
WORKDIR /app
COPY tempwatch.py .
USER 1000:1000
CMD ["python", "tempwatch.py"]
