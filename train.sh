#!/usr/bin/env bash
# WSN — шаг 2: обучение модели вашей квартиры по записи обхода из приложения.
#
#   ./train.sh              новая запись обхода (+ прежние на тех же платах и записи «пусто»)
#   ./train.sh --check      только честная проверка качества, модель не менять
#   ./train.sh --sessions ID1,ID2   конкретные записи
#   ./train.sh --nets 16    больше сетей в ансамбле (дольше, чуть устойчивее)
#
# Обучение идёт на этом компьютере, на всех ядрах процессора; время зависит от его мощности и длины записи
# (обычно 1–10 минут). Сервер подхватывает новую модель сам, перезапускать ничего не нужно.
# Прежняя модель остаётся в data/model.prev — откат: ./train.sh --rollback
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$PWD
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) ARGS+=(--dry-run) ;;
    --rollback)
      [ -d data/model.prev ] || { echo "нет прежней модели (data/model.prev)" >&2; exit 1; }
      rm -rf data/model.bad && { [ -d data/model ] && mv data/model data/model.bad || true; } && mv data/model.prev data/model
      echo "вернулась прежняя модель; отклонённая — в data/model.bad"; exit 0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

"${COMPOSE[@]}" ps --status running api 2>/dev/null | grep -q api \
  || { echo "сервер не запущен: ./install.sh" >&2; exit 1; }
OLD=$("${COMPOSE[@]}" exec -T api python -c \
  "import json, urllib.request; m = json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/model'))['model'] or {}; print(m.get('version') or 0)")
printf '\033[1mОбучение модели квартиры (ядер процессора: %s)\033[0m\n' "$(nproc)"
START=$(date +%s)
# пониженный приоритет: сервер в том же контейнере продолжает принимать данные плат без потерь
"${COMPOSE[@]}" exec -T api nice -n 10 python -m wsn.train --jobs "$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))" "${ARGS[@]}"
printf '\nВремя: %s с\n' "$(( $(date +%s) - START ))"
if [[ " ${ARGS[*]} " != *" --dry-run "* ]]; then
  if "${COMPOSE[@]}" exec -T api python -m wsn.cli wait-model "$OLD" --timeout 30 >/dev/null; then
    printf '\033[32mСервер работает с новой моделью.\033[0m Откройте «Карту» в приложении.\n'
  else
    echo "сервер ещё не подхватил модель — посмотрите: ${COMPOSE[*]} logs --tail 50 api" >&2
  fi
fi
