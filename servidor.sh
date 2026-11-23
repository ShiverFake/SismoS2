#!/usr/bin/env bash
# Controla o servidor web em segundo plano
cd "$(dirname "$0")"
PIDFILE=server.pid

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

start() {
  if running; then echo "Já está rodando (PID $(cat $PIDFILE))."; return; fi
  nohup .venv/bin/python run.py > server.log 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  sleep 2
  if running; then
    echo "Servidor iniciado: http://$(hostname -I | awk '{print $1}'):5000  (PID $(cat $PIDFILE))"
  else
    echo "Falhou ao iniciar. Últimas linhas do log:"; tail -20 server.log
  fi
}

stop() {
  if running; then kill "$(cat "$PIDFILE")" && echo "Servidor parado."; else echo "Não está rodando."; fi
  rm -f "$PIDFILE"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) running && echo "Rodando (PID $(cat $PIDFILE))" || echo "Parado" ;;
  log) tail -f server.log ;;
  *) echo "Uso: bash servidor.sh start|stop|restart|status|log" ;;
esac
