# Деплой на Vercel

## Важно: что именно едет на Vercel

**Сам Python-бот (uvicorn, фоновый цикл, SQLite, WebSocket-сервер) на Vercel не запускается** — там serverless и жёсткие лимиты по времени, нет постоянного процесса.

Рабочая схема:

| Где | Что |
|-----|-----|
| **Railway / Render / Fly / Docker** | Бэкенд: `main.py`, реальное время, `/api`, `/ws` |
| **Vercel** | Только **статический дашборд** из папки `dashboard/` |

Дашборд при открытии с `*.vercel.app` ходит на **ваш URL бэкенда** (переменная **`API_BASE_URL`** при сборке).

## 1. Поднять бэкенд

См. [DEPLOY.md](./DEPLOY.md). Получите публичный HTTPS URL, например `https://polymarket-bot-production.up.railway.app`.

В **Environment** бэкенда добавьте:

```env
CORS_ORIGINS=https://your-app.vercel.app
```

Если несколько доменов — через запятую, без пробелов лишних:

```env
CORS_ORIGINS=https://foo.vercel.app,https://www.bar.com
```

Без `CORS_ORIGINS` браузер заблокирует `fetch` с дашборда Vercel на другой origin.

## 2. Проект на Vercel

1. [vercel.com](https://vercel.com) → **Add New** → **Project** → импорт репозитория.
2. **Root Directory**: `polymarket-bot` (если в репо только эта папка — оставьте корень).
3. Framework: **Other** (или авто с `vercel.json`).
4. **Build Command**: `npm run build` (уже в `vercel.json`).
5. **Output Directory**: `dashboard`.
6. **Environment Variables** (для *Production* и *Preview* по желанию):

   | Name | Value |
   |------|--------|
   | `API_BASE_URL` | `https://ваш-бэкенд.up.railway.app` — **без** слэша в конце |

7. **Deploy**.

Сборка выполнит `scripts/vercel-api-config.mjs` и запишет `dashboard/api-config.js` с `window.__API_BASE__ = "https://..."`.

## 3. Проверка

- Откройте сайт Vercel → статус **WebSocket · live**, баланс и цены обновляются.
- Если «висят» или CORS-ошибки в консоли — проверьте `CORS_ORIGINS` на бэкенде и точное совпадение `https://` + домен Vercel.

## Локально как на Vercel

```bash
cd polymarket-bot
API_BASE_URL=https://your-backend.example.com npm run build
# затем откройте dashboard/index.html через любой статический сервер или смотрите на Vercel preview
```

## Один домен только на Vercel

Полный стек **без** отдельного бэкенда на Vercel не поддерживается этой архитектурой. Если нужен один хостинг — используйте Railway/Render/Fly для всего приложения (см. `DEPLOY.md` + `Dockerfile`).
