# Build multi-étapes : les dépendances sont installées dans un venv, l'image finale
# ne contient ni compilateur ni cache pip, et tourne en utilisateur non root.
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.lock .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.lock

FROM python:3.12-slim
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /venv /venv
WORKDIR /srv
COPY app ./app
USER 10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
