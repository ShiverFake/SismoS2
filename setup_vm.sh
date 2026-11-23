#!/usr/bin/env bash
# Instala as dependências e cria o ambiente virtual (Ubuntu/Debian)
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -c "import ensurepip" 2>/dev/null; then
  echo ">> Instalando python3-venv e pip (pede a senha do sudo)"
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip
fi

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

echo ">> Rodando testes"
python -m pytest -q

if command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  echo ">> Liberando a porta 5000 no firewall"
  sudo ufw allow 5000/tcp
fi

echo
echo "Pronto! Para iniciar:  . .venv/bin/activate && python run.py"
echo "Depois acesse:         http://$(hostname -I | awk '{print $1}'):5000"
