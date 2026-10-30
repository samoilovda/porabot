# Mini App (`bot/webapp/`)

Static frontend served by `bot/services/webserver.py`; data comes from `/api/miniapp/*`, authenticated by Telegram `initData` HMAC (`bot/services/miniapp.py`).

- Plain HTML/JS/CSS, no build step, no bundler. Keep it that way.
- Files are served from a hand-written map in `webserver.py`, not a static directory. A new file in this folder is not served until it is registered there.
- Strings are NOT in `bot/lexicon/`. `app.js` has its own `STRINGS` table (`en`, `ru`, `es`, `pt`). Add each new string to all four; unknown language falls back to `en` (unlike the bot, which falls back to `ru`).
- Language comes from the bot profile (`/api/miniapp/profile`), not from the Telegram client locale; the client locale is only the initial guess.
- Never trust a user id from the query string or body; the server takes it from validated `initData` only.
- Tests: `npm test` (`tests/frontend/miniapp.test.cjs` uses linkedom; `browser.test.cjs` needs Chrome via `CHROMIUM_PATH`, see root `AGENTS.md`). They read these files directly, so run them after any change here.
- Telegram requires HTTPS for the Mini App URL; the web server is off by default (`WEB_SERVER_ENABLED`).
