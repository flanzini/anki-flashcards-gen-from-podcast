# Deploy transcript service to Google Cloud

Phone-triggered flow: Shortcut → Cloud Run → Speech-to-Text → GCS → email with signed download URL.

**Status:** optional. GCP deployment is on hold when free credits are unavailable.
For routine listening, use local `--transcribe-only` batch prep in `episode_to_anki.py`
and sync `transcripts/` to your phone. This service remains in the repo for future
on-demand use or as a reference for a self-hosted variant.

## Prerequisites

- GCP project with billing
- `gcloud` CLI authenticated
- APIs enabled: Cloud Run, Cloud Storage, Speech-to-Text, Cloud Tasks, Secret Manager

```bash
gcloud services enable run.googleapis.com storage.googleapis.com speech.googleapis.com cloudtasks.googleapis.com secretmanager.googleapis.com
```

## One-time infrastructure

```bash
export PROJECT_ID=your-project
export REGION=europe-west1
export BUCKET=${PROJECT_ID}-ulp-transcripts

gcloud config set project "$PROJECT_ID"
gsutil mb -l "$REGION" "gs://${BUCKET}"

gcloud tasks queues create transcript-jobs --location="$REGION"

# Store secrets (examples)
echo -n 'long-random-token' | gcloud secrets create transcript-api-token --data-file=-
echo -n 'smtp.example.com' | gcloud secrets create smtp-host --data-file=-
# ... smtp-user, smtp-password, email-from, email-to
```

## Build and deploy Cloud Run

From the repository root:

```bash
gcloud builds submit --tag "gcr.io/${PROJECT_ID}/ulp-transcript-service" -f cloud/Dockerfile .

gcloud run deploy ulp-transcript-service \
  --image "gcr.io/${PROJECT_ID}/ulp-transcript-service" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 3600 \
  --cpu-boost \
  --set-env-vars "GCS_BUCKET=${BUCKET},TRANSCRIPT_SPEECH_BACKEND=google,CLOUD_TASKS_PROJECT=${PROJECT_ID},CLOUD_TASKS_LOCATION=${REGION},CLOUD_TASKS_QUEUE=transcript-jobs" \
  --set-secrets "TRANSCRIPT_API_TOKEN=transcript-api-token:latest,SMTP_HOST=smtp-host:latest,SMTP_USER=smtp-user:latest,SMTP_PASSWORD=smtp-password:latest,EMAIL_FROM=email-from:latest,EMAIL_TO=email-to:latest"
```

After the first deploy, set the worker URL to the service URL:

```bash
export SERVICE_URL=$(gcloud run services describe ulp-transcript-service --region "$REGION" --format='value(status.url)')

gcloud run services update ulp-transcript-service \
  --region "$REGION" \
  --update-env-vars "TRANSCRIPT_WORKER_URL=${SERVICE_URL},PUBLIC_BASE_URL=${SERVICE_URL}"
```

Grant the Cloud Run service account permission to:

- read/write the GCS bucket
- create Cloud Tasks
- use Speech-to-Text
- sign GCS URLs (may require a user-managed key or IAM SignBlob on the service account)

`--allow-unauthenticated` exposes the HTTPS endpoint; the app still requires `Authorization: Bearer <TRANSCRIPT_API_TOKEN>` on `/v1/*` and the worker route.

## Local dry run (no GCP)

```powershell
cd cloud
pip install -r requirements.txt
$env:TRANSCRIPT_API_TOKEN="dev-token"
$env:EMAIL_TO="you@example.com"
$env:TRANSCRIPT_SPEECH_BACKEND="mock"
$env:LOCAL_DATA_DIR="..\cloud_data"
uvicorn transcript_service.app:app --reload --port 8080
```

```powershell
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8080/v1/transcripts `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{ "episode_index": 0 }'
```

With `mock` speech and no SMTP, the download URL is logged and stored under `cloud_data/`.

## Phone shortcut

1. HTTP Request: `POST {{SERVICE_URL}}/v1/transcripts`
2. Header: `Authorization: Bearer <token>`
3. JSON body: `{ "episode_index": 0 }` (newest) or `{ "title_search": "doctor" }`
4. Open the emailed signed URL when the job finishes (`GET /v1/jobs/{job_id}` to poll).

## Manual smoke test after deploy

1. `POST /v1/transcripts` with `episode_index: 0` → expect `202` and a `job_id` (or `200` on cache hit).
2. Confirm object `transcripts/<key>.txt` in the bucket.
3. Confirm email with a working signed URL.
4. Repeat the same request → expect immediate `200` cache path.
