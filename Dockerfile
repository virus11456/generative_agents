FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Default command runs the environment (frontend) server; the simulation
# backend is started interactively via `docker compose run backend`.
WORKDIR /app/environment/frontend_server
CMD ["gunicorn", "frontend_server.wsgi", "-b", "0.0.0.0:8000", "--timeout", "600"]
