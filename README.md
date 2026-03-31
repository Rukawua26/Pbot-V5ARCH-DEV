# Pbot V5ARCH DEV

Bot de trading cuantitativo para Binance Futures con:

- triaje dinamico de pares por liquidez
- consenso de agentes (Trinity: MT, SR, G)
- filtro SHOCK por distancia estructural
- modo real y modo shadow

## Requisitos

- Python 3.10+
- Dependencias en `requirements.txt`

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuracion

- Define variables sensibles en `.env` (no versionado).
- Revisa parametros en:
  - `core/config/operational.py`
  - `core/config/strategy.py`
  - `core/config/manager.py`

## Ejecucion local

```bash
python3 main.py
```

## Ejecutar como servicio (systemd)

Archivo recomendado: `sniper-ai.service`

```bash
sudo cp sniper-ai.service /etc/systemd/system/sniper-ai.service
sudo systemctl daemon-reload
sudo systemctl enable sniper-ai.service
sudo systemctl restart sniper-ai.service
sudo systemctl status sniper-ai.service --no-pager
```

## Logs

```bash
tail -f sniper.log
```

## Seguridad

- No subas `.env`, DBs, logs o modelos binarios.
- Usa `.gitignore` para artefactos locales.
