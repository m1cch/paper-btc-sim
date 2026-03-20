# Бесплатный деплой (Render)

В репозитории уже лежит **`render.yaml`**: после привязки репо сервис поднимется сам.

## Зачем Render (free)

- **0 $** для одного **Web Service** на бесплатном плане (инстанс засыпает без трафика — первый запрос может подождать ~1 мин).
- Подходит нашему стеку: **Docker** + **uvicorn** + **WebSocket**.

Ограничение: **SQLite без платного диска** живёт в контейнере и **сбросится** при пересборке/перезапуске. Для постоянной истории позже можно добавить **Persistent Disk** в платном тарифе или другой хостинг.

## Шаги (≈5 минут)

1. **GitHub** — репозиторий с `Dockerfile` и `render.yaml` в **корне** (как в этом проекте).

2. **Render**  
   [render.com](https://render.com) → **Sign in with GitHub**.

3. **Blueprint**  
   **Dashboard** → **Blueprints** → **New Blueprint Instance** → выбери репозиторий → Render найдёт **`render.yaml`** и создаст сервис `paper-btc-sim`.

   *Если Blueprint не предлагается:* **New** → **Web Service** → тот же репо → **Docker**, root directory — корень, **Dockerfile** в корне, **Health Check Path**: `/api/health`.

4. **URL**  
   После деплоя открой выданный URL `https://paper-btc-sim-....onrender.com` — там дашборд и live-данные.

5. **Переменные** (уже в `render.yaml`, при необходимости правь в Dashboard):  
   `AUTO_TRADE`, `INITIAL_DEPOSIT`, `GAMMA_SERIES_ID`.

## Локально перед пушем

```bash
cd paper-btc-sim
git init
git add .
git commit -m "paper btc sim"
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## Альтернативы с free tier

- **Railway** — часто даёт trial/кредиты; подключение репо похоже.  
- **Fly.io** — есть бесплатный allowance, нужен `fly launch` и карта (часто).

Для «максимально просто и бесплатно» с готовым `render.yaml` удобнее всего **Render**.
