# Paper BTC Sim

Виртуальный трейдер по **BTC 15m** окнам: **FastAPI**, дашборд, **WebSocket**, SQLite. Реальные ордера не отправляются.

## Развернуть на Render

1. Нажми кнопку (аккаунт [Render](https://render.com), вход через GitHub).
2. Подтверди деплой по `render.yaml`.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/m1cch/paper-btc-sim)

Репозиторий: **https://github.com/m1cch/paper-btc-sim**

Подробнее: [DEPLOY_FREE.md](./DEPLOY_FREE.md) · [DEPLOY.md](./DEPLOY.md) · UI на Vercel: [DEPLOY_VERCEL.md](./DEPLOY_VERCEL.md)

## Локально

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Открой `http://127.0.0.1:8080` (или порт из `.env`).
