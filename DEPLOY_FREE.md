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

## После создания Blueprint — что делать дальше

1. **Дождись деплоя**  
   В Render: **Dashboard** → твой сервис **`paper-btc-sim`** → вкладка **Logs**.  
   Успех: строки вида `Uvicorn running on http://0.0.0.0:...` и `Application startup complete`.  
   Ошибка сборки — смотри лог **Build** (Dockerfile, зависимости).

2. **Открой URL**  
   В карточке сервиса сверху ссылка **`https://paper-btc-sim-xxxx.onrender.com`** (точное имя у тебя в Dashboard).  
   Должен открыться дашборд. На **free** первый заход после простоя может **подвиснуть ~1 мин** — инстанс просыпается.

3. **Проверка API**  
   В браузере: `https://твой-сервис.onrender.com/api/health` → JSON с `"ok": true`.  
   Если 502/503 — подожди минуту и обнови (сервис ещё стартует).

4. **Проверка в браузере**  
   В шапке дашборда — **WebSocket · live**, бегут цены YES/NO. Если «Переподключение…» — подожди 10–20 сек и обнови страницу.

5. **Переменные** (если нужно)  
   **Settings** → **Environment** → правь `AUTO_TRADE`, `INITIAL_DEPOSIT`, `GAMMA_SERIES_ID` → **Save deploys** (пересоберётся).

6. **Обновления**  
   Пуш в `main` на GitHub обычно **автоматически** пересобирает сервис (если в репо включён auto-deploy). Иначе: **Manual Deploy** → **Deploy latest commit**.

7. **Если что-то не так**  
   - **Build failed** — лог сборки Docker; приватный репо: проверь [GitHub App Render](https://github.com/apps/render).  
   - **Health check failed** — в `render.yaml` указан `healthCheckPath: /api/health`; убедись, что контейнер слушает `PORT` (у нас так).  
   - **Пустой рынок** — иногда Gamma API не отдаёт окно; подожди 1–2 минуты.

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
