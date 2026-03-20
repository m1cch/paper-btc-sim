# Деплой paper-бота в облако

**Бесплатно и без своего сервера:** готовый **[DEPLOY_FREE.md](./DEPLOY_FREE.md)** (Render + `render.yaml`).

Бот — это один процесс **FastAPI + uvicorn**: дашборд по `/`, API и **WebSocket** по `/ws`. Достаточно открыть выданный URL в браузере — котировки и PnL обновляются в реальном времени, локальный компьютер не нужен.

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `PORT` | Задаётся **автоматически** на Render, Railway, Fly и т.п. Не переопределяйте без нужды. |
| `DASHBOARD_PORT` | Только если запускаете сами (`python main.py`) без `PORT`. |
| `DATA_DIR` | Каталог для `db/trades.db` и `trades.log`. В Docker по умолчанию лучше **`/data`** + диск у хостинга, иначе после редеплоя история обнулится. |
| Остальное | Скопируйте из `.env.example` ( `AUTO_TRADE`, `GAMMA_SERIES_ID`, `INITIAL_DEPOSIT`, … ). |

Секретов для **paper**-режима нет; реальные ключи Polymarket не используются.

## Вариант A — Docker (универсально)

Из каталога `polymarket-bot`:

```bash
docker build -t polymarket-paper-bot .
docker run --rm -p 8080:8080 \
  -e AUTO_TRADE=true \
  -e DATA_DIR=/data \
  -v polymarket-data:/data \
  polymarket-paper-bot
```

Откройте `http://localhost:8080`.

## Вариант B — [Railway](https://railway.app)

1. New Project → **Deploy from GitHub** (или загрузка репозитория).
2. Укажите **Root Directory**: `polymarket-bot`, если репозиторий — монорепо.
3. Railway подхватит **Dockerfile** сама.
4. В **Variables** добавьте, например: `AUTO_TRADE=true`, `INITIAL_DEPOSIT=300`, при желании `DATA_DIR=/data` и подключите **Volume**, смонтированный в `/data` (тогда история сделок сохранится между деплоями).

После деплоя откройте выданный **HTTPS** URL — WebSocket подключится как `wss://…/ws` автоматически.

## Вариант C — [Render](https://render.com)

1. **New** → **Web Service** → подключите репозиторий.
2. **Root Directory**: `polymarket-bot`.
3. **Runtime**: Docker **или** Native: Build `pip install -r requirements.txt`, Start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
4. Добавьте env vars (как в Railway). Для постоянной БД: **Disk** → mount path, например `/data`, и переменная `DATA_DIR=/data`.

**Free**-план засыпает без трафика; для 24/7 нужен платный инстанс или другой хостинг.

## Вариант D — [Fly.io](https://fly.io)

```bash
cd polymarket-bot
fly launch --dockerfile Dockerfile
# Добавьте volume для /data и DATA_DIR=/data в fly.toml / secrets при необходимости.
```

## Проверка после деплоя

- `GET /api/health` → `{"ok": true, ...}`
- Главная страница → статус **WebSocket · live**
- В шапке дашборда — баланс и сделки в реальном времени

## Ограничения

- **SQLite** на диске без тома **пропадает** при новом деплое/пересоздании контейнера — задайте `DATA_DIR` на смонтированный том.
- Хостинг должен поддерживать **долгие WebSocket** и не резать idle (у бесплатных тиров иногда есть лимиты).

## Vercel

Сам бот на Vercel **не** крутится (serverless). Туда выкладывается **только дашборд**, API/WS остаются на Railway/Render и т.д. Подробности: **[DEPLOY_VERCEL.md](./DEPLOY_VERCEL.md)**.
