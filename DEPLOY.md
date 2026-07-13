# Развёртывание на своём Linux-сервере

Стек: веб-интерфейс (FastAPI) + Telegram-бот, оба на одной SQLite-базе, всё в Docker Compose.

## 1. Снести старый Telegram-бот

Раньше бот, скорее всего, был запущен одним из способов ниже. Найди свой и убери его, чтобы не было двух процессов с одним `BOT_TOKEN` (иначе Telegram будет отдавать апдейты то одному, то другому).

### Если бот работал как systemd-сервис

```bash
# найти имя сервиса (часто tg-bot, tgbot, life-tracker и т.п.)
systemctl list-units --type=service | grep -iE 'bot|tracker'

# остановить и отключить автозапуск (подставь реальное имя)
sudo systemctl stop <имя>.service
sudo systemctl disable <имя>.service

# удалить unit-файл и перезагрузить systemd
sudo rm /etc/systemd/system/<имя>.service
sudo systemctl daemon-reload
```

### Если бот работал в screen/tmux или просто в фоне

```bash
# найти процесс
ps aux | grep -iE 'bot/main|python.*bot' | grep -v grep

# убить по PID
kill <PID>          # если не помогает: kill -9 <PID>

# если это была сессия screen/tmux
screen -ls          # затем: screen -X -S <id> quit
tmux ls             # затем: tmux kill-session -t <name>
```

### Если бот работал в отдельном Docker-контейнере

```bash
docker ps | grep -iE 'bot|tracker'
docker stop <container>
docker rm <container>
# если был свой compose-проект:
# cd /path/to/old-project && docker compose down
```

### Проверить, что процессов с ботом не осталось

```bash
ps aux | grep -iE 'bot/main|python.*bot' | grep -v grep   # должно быть пусто
```

> ⚠️ Если старый бот писал в свою базу `life_tracker.db` и данные нужны — сохрани файл
> (`cp /path/to/old/life_tracker.db ~/life_tracker.backup.db`) перед сносом. Ниже описано,
> как переиспользовать эту базу в новом стеке.

## 2. Поднять новый стек

Нужны установленные Docker и docker compose.

```bash
# 1. Забрать код
git clone https://github.com/ruby-rs/tg-bot-1.git
cd tg-bot-1

# 2. Создать .env из примера и заполнить
cp .env.example .env
nano .env
```

В `.env` заполни:

- `BOT_TOKEN` — токен бота от @BotFather (тот же, что был).
- `WEB_PASSWORD` — пароль для входа на сайт.
- `WEB_SECRET_KEY` — длинная случайная строка. Сгенерировать:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- `WEB_PORT` — порт, на котором открыть сайт (по умолчанию 8000).
- `WEB_USER_TG_ID` — чтобы веб и бот вели **одного** пользователя, впиши свой Telegram id
  (узнать: напиши боту @userinfobot). Если оставить `0` — веб будет отдельным пользователем.

```bash
# 3. Собрать и запустить (веб + бот)
docker compose up -d --build

# 4. Проверить логи
docker compose logs -f
```

Сайт будет доступен на `http://<ip-сервера>:8000`. Открой на телефоне, введи `WEB_PASSWORD`.

### (Опционально) перенести старую базу

Если сохранял `life_tracker.db` от старого бота и хочешь продолжить на тех же данных:

```bash
# узнать путь тома новой базы
docker volume inspect tg-bot-1_tracker-data

# остановить стек, скопировать базу в том, запустить снова
docker compose down
docker run --rm -v tg-bot-1_tracker-data:/data -v $HOME:/backup alpine \
    cp /backup/life_tracker.backup.db /data/life_tracker.db
docker compose up -d
```

## 3. HTTPS и домен (рекомендуется)

Сайт отдаёт пароль в открытую, поэтому для доступа извне подними HTTPS. Проще всего —
обратный прокси (nginx/Caddy) с Let's Encrypt перед контейнером `web`. Пример для Caddy:

```
tracker.example.com {
    reverse_proxy localhost:8000
}
```

## Обновление до новой версии

```bash
cd tg-bot-1
git pull
docker compose up -d --build
```

## Полезные команды

```bash
docker compose ps            # статус сервисов
docker compose logs -f web   # логи веба
docker compose logs -f bot   # логи бота
docker compose restart       # перезапуск
docker compose down          # остановить (данные в томе сохраняются)
```
