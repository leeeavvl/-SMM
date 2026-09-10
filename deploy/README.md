# Деплой «Контент-завода» на сервер

Пошаговая инструкция, чтобы выложить приложение на VPS и дать доступ
нескольким людям по общей ссылке (один общий сайт, один бренд, общие данные).

## 1. Арендовать сервер

Любой VPS с Ubuntu 22.04, 2 ГБ RAM (Timeweb Cloud / Beget / Hetzner и т.п.).
Подробности — см. переписку с Claude или любой гайд по аренде VPS.

## 2. Домен

Купить домен, в DNS добавить A-запись на IP сервера.

## 3. Установить окружение на сервере

```bash
apt update && apt install -y python3.12-venv nginx git certbot python3-certbot-nginx apache2-utils
```

## 4. Залить код

```bash
git clone <ссылка-на-репозиторий> /opt/smm-app
cd /opt/smm-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Скопируйте вручную (НЕ через git) секретные файлы, если используете:
- JSON-ключ сервисного аккаунта Google (для выгрузки в Google Таблицу)

## 5. Запустить как systemd-сервис

```bash
cp deploy/smm-app.service /etc/systemd/system/smm-app.service
systemctl daemon-reload
systemctl enable --now smm-app
systemctl status smm-app
```

API-ключи ИИ-провайдеров (Claude/OpenAI/Gemini) можно не прописывать в
systemd-юните — их проще один раз ввести в самом приложении через
**Настройки**, они сохранятся в базе на сервере.

## 6. Nginx + пароль на вход (обязательно!)

⚠️ У приложения нет своей авторизации — без пароля к нему сможет зайти
кто угодно в интернете и использовать ваши API-ключи.

```bash
htpasswd -c /etc/nginx/.smm-app-htpasswd ваш_логин
cp deploy/nginx.conf.example /etc/nginx/sites-available/smm-app
nano /etc/nginx/sites-available/smm-app   # замените ваш-домен.ру
ln -s /etc/nginx/sites-available/smm-app /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d ваш-домен.ру
```

## 7. Обновить Canva Redirect URI

В интеграции на canva.com/developers добавьте ещё один Redirect URI —
под боевой домен:
```
https://ваш-домен.ру/api/canva/oauth/callback
```
(старый `http://127.0.0.1:8000/...` можно оставить для локальной разработки)

## 8. Дать доступ людям

Просто передайте им:
- ссылку `https://ваш-домен.ру`
- логин/пароль от Basic Auth (заданные на шаге 6)

Все будут работать в одном общем интерфейсе с общими данными — как
несколько человек в одной команде.

## Обновление после изменений в коде

```bash
cd /opt/smm-app
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart smm-app
```
