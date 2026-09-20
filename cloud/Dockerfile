FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY cloud/requirements.txt /app/cloud/requirements.txt
RUN pip install --no-cache-dir -r /app/cloud/requirements.txt

COPY podcast_to_anki.py /app/podcast_to_anki.py
COPY cloud/transcript_service /app/transcript_service

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "transcript_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
