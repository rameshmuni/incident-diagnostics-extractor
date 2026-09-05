# One image, two Cloud Run resources built from it:
#   - deployed as a Cloud Run SERVICE, it serves app.py (this file's default CMD)
#   - deployed as a Cloud Run JOB, the deploy command overrides the CMD to run
#     run_refresh.py instead - same image, same dependencies, different entrypoint.
# That's why this Dockerfile doesn't hardcode which script "the app" is - both
# app.py and run_refresh.py ship inside every build.

FROM python:3.11-slim

WORKDIR /app

# install dependencies first, in their own layer, so `docker build` only re-installs
# them when requirements.txt actually changes - not on every code edit
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# everything else: app.py, query_incident*.py, static/, etc. Notably NOT any local index
# files anymore - the corpus lives in BigQuery now, not baked into this image, so refreshing
# it (via the Cloud Run Job) never requires rebuilding or redeploying this image at all.
# (.dockerignore keeps out the stuff that shouldn't be in the image - see that file)
COPY . .

# Cloud Run sets PORT itself at runtime (usually 8080) and expects the container to
# listen on 0.0.0.0:$PORT - gunicorn's $PORT below reads that same env var
ENV PORT=8080
EXPOSE 8080

# production WSGI server, not Flask's built-in dev server (that one isn't meant to
# be exposed to real traffic) - "app:app" means "the `app` object inside app.py"
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 app:app
