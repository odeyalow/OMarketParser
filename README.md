# OMarket Parser

Веб-интерфейс для парсинга `omarket.kz` с быстрым backend на Python/Flask.

## Что делает проект

- принимает поисковый запрос
- проходит по страницам выдачи OMarket
- собирает ENSTRU-строки из карточек товаров
- показывает прогресс, позволяет остановить парсер
- хранит результаты в памяти процесса
- экспортирует результат в Excel

## Важное ограничение

Проект хранит состояние парсинга в памяти Python-процесса.

Из-за этого:

- запускать нужно только в `1` worker
- нельзя поднимать несколько экземпляров backend одновременно
- после перезапуска процесса история и таблица очищаются

## Локальный запуск на Windows

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
.\run_ui.bat
```

После запуска открой `http://127.0.0.1:5050`.

## Локальный запуск на Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x run_ui.sh
./run_ui.sh
```

После запуска открой `http://127.0.0.1:5050`.

## Деплой в Proxmox LXC / VM

Ниже пример для `Debian 12` или `Ubuntu 22.04/24.04`.

### 1. Установить системные пакеты

```bash
apt update
apt install -y git python3 python3-venv python3-pip nginx
```

### 2. Забрать проект с GitHub

```bash
cd /opt
git clone <URL_ТВОЕГО_GITHUB_РЕПО> omarket-parser
cd /opt/omarket-parser
```

### 3. Подготовить запуск

```bash
chmod +x run_ui.sh
./run_ui.sh
```

Это проверочный запуск. Приложение поднимется на `127.0.0.1:5050`.

Остановить можно через:

```bash
Ctrl+C
```

### 4. Подключить systemd

Скопируй готовый unit:

```bash
cp deploy/systemd/omarket-parser.service /etc/systemd/system/omarket-parser.service
```

Если нужно, поправь в unit:

- `User`
- `Group`
- `WorkingDirectory`
- `ExecStart`

По умолчанию там путь:

- `/opt/omarket-parser`

Затем включи сервис:

```bash
systemctl daemon-reload
systemctl enable --now omarket-parser
systemctl status omarket-parser
```

### 5. Подключить Nginx

Скопируй конфиг:

```bash
cp deploy/nginx/omarket-parser.conf /etc/nginx/sites-available/omarket-parser.conf
ln -s /etc/nginx/sites-available/omarket-parser.conf /etc/nginx/sites-enabled/omarket-parser.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
```

После этого приложение будет доступно по IP контейнера на `80` порту.

## Полезные команды на сервере

Перезапуск сервиса:

```bash
systemctl restart omarket-parser
```

Логи сервиса:

```bash
journalctl -u omarket-parser -f
```

Проверка Nginx:

```bash
nginx -t
```

## Production-запуск

На Linux проект запускается через `gunicorn`:

```bash
gunicorn -w 1 -b 127.0.0.1:5050 --timeout 300 wsgi:app
```

Именно `-w 1`, потому что иначе состояние парсера будет расходиться между воркерами.

