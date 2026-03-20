# Бесплатный деплой (Render)

Я **не могу** зайти в твой аккаунт и нажать «Deploy» за тебя — нужен твой GitHub и логин на [render.com](https://render.com). В репозитории уже лежит **`render.yaml`**: после привязки репо сервис поднимется сам.

## Зачем Render (free)

- **0 $** для одного **Web Service** на бесплатном плане (инстанс засыпает без трафика — первый запрос может подождать ~1 мин).
- Подходит нашему стеку: **Docker** + **uvicorn** + **WebSocket**.

Ограничение: **SQLite без платного диска** живёт в контейнере и **сбросится** при пересборке/перезапуске. Для постоянной истории позже можно добавить **Persistent Disk** в платном тарифе или другой хостинг.

## Шаги (≈5 минут)

1. **GitHub**  
   Залей эту папку `polymarket-bot` в **новый репозиторий** (корень репозитория = содержимое `polymarket-bot`, чтобы `Dockerfile` и `render.yaml` лежали в корне).

2. **Render**  
   Зарегистрируйся на [render.com](https://render.com) → **Sign in with GitHub**.

3. **Blueprint**  
   **Dashboard** → **Blueprints** → **New Blueprint Instance** → выбери репозиторий → Render найдёт **`render.yaml`** и создаст сервис `polymarket-paper-bot`.

   *Если Blueprint не предлагается:* **New** → **Web Service** → тот же репо → **Docker**, root directory — корень, **Dockerfile** в корне, **Health Check Path**: `/api/health`.

4. **URL**  
   После деплоя открой выданный URL `https://polymarket-paper-bot-....onrender.com` — там дашборд и live-данные.

5. **Переменные** (уже в `render.yaml`, при необходимости правь в Dashboard):  
   `AUTO_TRADE`, `INITIAL_DEPOSIT`, `GAMMA_SERIES_ID`.

## Локально перед пушем

```bash
cd polymarket-bot
git init
git add .
git commit -m "polymarket paper bot"
# создай репо на GitHub и:
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## Альтернативы с free tier

- **Railway** — часто даёт trial/кредиты; подключение репо похоже.  
- **Fly.io** — есть бесплатный allowance, нужен `fly launch` и карта (часто).

Для «максимально просто и бесплатно» с готовым `render.yaml` удобнее всего **Render**.
