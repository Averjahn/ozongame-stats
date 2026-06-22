#!/usr/bin/env bash
# Деплой ozongame stats на сервер + настройка cron
# Запуск: ./deploy.sh
set -euo pipefail

SSH_HOST="webadmin@edudev.med-game.ru"
SSH_PASS="MedSite128"
REMOTE_DIR="/home/webadmin/ozongame-stats"
STATS_URL="http://edudev.med-game.ru/stats/"

echo "=== OzonGame Stats Deploy ==="
echo "Target: $SSH_HOST:$REMOTE_DIR"
echo ""

# 1) Создать директорию на сервере и залить скрипт
echo "[1/4] Заливаем generate.py …"
sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no "$SSH_HOST" "mkdir -p $REMOTE_DIR"
sshpass -p "$SSH_PASS" scp -o StrictHostKeyChecking=no generate.py "$SSH_HOST:$REMOTE_DIR/generate.py"

# 2) Проверить psycopg2
echo "[2/4] Проверяем psycopg2 …"
sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no "$SSH_HOST" \
  "python3 -c 'import psycopg2' 2>/dev/null || pip3 install --quiet psycopg2-binary"

# 3) Первый запуск — генерируем сразу
echo "[3/4] Первый запуск генерации …"
sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no "$SSH_HOST" \
  "DB_PASSWORD=Visual101 python3 $REMOTE_DIR/generate.py --output /var/www/mysite/stats/index.html"

# 4) Настроить cron (каждый день в 04:00)
echo "[4/4] Настраиваем cron (ежедневно в 04:00) …"
CRON_LINE="0 4 * * * DB_PASSWORD=Visual101 python3 $REMOTE_DIR/generate.py --output /var/www/mysite/stats/index.html >> $REMOTE_DIR/generate.log 2>&1"
sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=no "$SSH_HOST" \
  "(crontab -l 2>/dev/null | grep -v 'ozongame-stats'; echo '$CRON_LINE') | crontab -"

echo ""
echo "=== Готово ==="
echo "Дашборд доступен по адресу: $STATS_URL"
echo "Логи генерации: $REMOTE_DIR/generate.log"
echo "Обновляется автоматически каждый день в 04:00."
