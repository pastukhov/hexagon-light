# Hexagon Light (MeRGBW / TG609) — управление по BLE

Этот репозиторий содержит простой standalone-контроллер для BLE-светильника **Hexagon Light** (приложение **MeRGBW**, модель/прошивка **TG609**).

Цель: управление через Bluetooth LE (GATT) без Home Assistant.

## Требования

- Linux с доступом к Bluetooth-адаптеру (BlueZ)
- Python 3.10+
- `bleak`

Установка зависимости:

```bash
python3 -m pip install bleak
```

## Быстрый старт (CLI)

MAC по умолчанию уже задан (`FF:FF:11:52:AB:BD`), но лучше указывать явно:

```bash
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD on
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD off
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD rgb 255 100 50
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD brightness 80
```

Команда `set` (несколько действий за один запуск):

```bash
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD --wait 2 set --power on --rgb 255 100 50 --brightness 80
```

`--wait SECONDS` — после выполнения команды дождаться уведомления и вывести best-effort статус.

## Docker

Сборка:

```bash
docker build -t hexagon-light .
```

Запуск (нужен доступ к system D-Bus BlueZ на хосте):

```bash
docker run --rm --network host \
  -v /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket \
  -e DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket \
  hexagon-light --mac FF:FF:11:52:AB:BD on
```

## Статус устройства

Команда `status` делает запрос синхронизации и пытается разобрать ответ из notifications:

```bash
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD status
```

Вывод примерно такой:

```text
is_on=True brightness=14 raw=...
```

## Сцены (эффекты)

В прошивке есть встроенные сцены/эффекты. Их можно включать по имени или индексу:

```bash
python3 hexagon_light.py scenes
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD scene symphony
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD scene 2
python3 hexagon_light.py --mac FF:FF:11:52:AB:BD scene aurora --speed 50
```

Список имён/индексов (TG609) хранится в `SCENES_TG609` внутри `hexagon_light.py`.

## Использование как библиотека

```python
from hexagon_light import HexagonLight

lamp = HexagonLight("FF:FF:11:52:AB:BD")
lamp.connect()
lamp.turn_on()
lamp.set_rgb(255, 100, 50)
lamp.set_brightness(80)
lamp.set_scene_by_name("symphony", speed=50)
print(lamp.get_state(wait_s=2.0))
lamp.disconnect()
```

## Troubleshooting

### Нет доступа к Bluetooth (Linux)

Если вы запускаете из sandbox/контейнера или без прав, управление BLE может быть недоступно. Симптомы:
- ошибки доступа к HCI сокету
- таймауты подключения

Проверьте, что Bluetooth включён и процесс имеет права на работу с адаптером.

### Устройство не отвечает на `status`

Некоторые устройства шлют notifications только после изменения состояния. Попробуйте:
- `on` / `brightness` / `rgb`, затем `status`
- увеличить `--wait`

## Что внутри

- `hexagon_ble.py` — вспомогательный исследовательский скрипт (дамп GATT, ручные write/notify)
- `hexagon_light.py` — основной контроллер (API + CLI)
