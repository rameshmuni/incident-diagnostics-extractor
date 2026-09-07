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

# Week 3's upload-permission demo scenario needs a REAL chmod to actually deny a write.
# On Linux, root ignores normal file-permission (DAC) checks entirely - so if this
# container ran as root (the Dockerfile default), a chmod 000 on the uploads folder
# would do nothing and the "broken" demo would keep working, silently faking the fault.
# Creating a dedicated non-root user and switching to it below is what makes that
# scenario a genuine fault instead of a fake one. mkdir the uploads folder (kept inside
# week3/ - see that package's mock_systems.py - so every Week 3 runtime artifact stays
# out of the shared scripts/ folder) and hand it, and everything else under /app, to
# that user before the switch, since root is the only one allowed to chown.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/week3/uploads \
    && chown -R appuser:appuser /app

USER appuser

# Cloud Run sets PORT itself at runtime (usually 8080) and expects the container to
# listen on 0.0.0.0:$PORT - gunicorn's $PORT below reads that same env var
ENV PORT=8080
EXPOSE 8080

# production WSGI server, not Flask's built-in dev server (that one isn't meant to
# be exposed to real traffic) - "app:app" means "the `app` object inside app.py"
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 app:app
