# Polymarket paper bot

Виртуальный бот по BTC 15m окнам Polymarket: **FastAPI**, дашборд, **WebSocket**, SQLite. Реальные сделки не отправляются.

## Развернуть бесплатно на Render (одна кнопка)

1. Нажми кнопку ниже (нужен аккаунт [Render](https://render.com), вход через GitHub).
2. Подключи репозиторий, если спросит — **Approve**.
3. Render подхватит [`render.yaml`](./render.yaml) и поднимет сервис. Через пару минут открой выданный URL `https://….onrender.com`.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/m1cch/polymarket-paper-bot)

Репозиторий: **https://github.com/m1cch/polymarket-paper-bot**

Подробнее: [DEPLOY_FREE.md](./DEPLOY_FREE.md) · Docker: [DEPLOY.md](./DEPLOY.md) · UI только на Vercel: [DEPLOY_VERCEL.md](./DEPLOY_VERCEL.md)

## Локально

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Открой `http://127.0.0.1:8080` (или `DASHBOARD_PORT` из `.env`).
