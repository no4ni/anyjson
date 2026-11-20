# Клиент симулятора робота (`hatasim2.py`)

Лёгкий клиент PyBullet-симулятора (18 КБ) встроен прямо в `anyjson`, а тяжёлые 3D-ассеты сцены (~106 МБ) вынесены в отдельный репозиторий:
https://github.com/no4ni/robosym

## Подключение ассетов

1. Клонируйте репозиторий с 3D-сценой:

    git clone https://github.com/no4ni/robosym.git
    pip install -r robosym/requirements.txt

2. Укажите путь к папке сцены в `side/config.json` проекта `anyjson`:

    {
      "sim_dir": "E:/Jericho/Private/projects/robosym"
    }