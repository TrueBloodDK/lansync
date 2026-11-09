# Демонстрация LANSync на двух Linux-машинах

Сценарий на 10–15 минут: Linux-хост и Linux-ВМ либо две Linux-ВМ.
Сервер и клиент используют разные папки и обмениваются по HTTPS через сеть.
Общая папка VirtualBox для синхронизируемых данных не нужна.
Windows-клиент проверен отдельно: [отчёт](CROSS_PLATFORM_TEST.md).

## Подготовить до встречи

Установите одну версию LANSync с extra `[setup]` на обеих машинах по INSTALL.md.
В командах ниже используются новые каталоги в домашней папке; для повторения
выберите новые имена. Старые проекты вручную не очищаются этим сценарием.
Проверьте IP сервера и доступность TCP 8765 от клиента; сертификат должен
содержать именно этот IP. Не отключайте проверку TLS для демонстрации.

На сервере (пример адреса; подставьте действующий):

```sh
SERVER_IP=192.168.0.108
lansync init "$HOME/demo-server" --mode server
lansync credentials create --host "$SERVER_IP" --output-dir "$HOME/demo-server/credentials"
lansync credentials export-client --source-dir "$HOME/demo-server/credentials" --output-dir "$HOME/demo-client-bundle"
lansync run "$HOME/demo-server/project.toml"
```

Последняя команда остаётся работать в отдельном терминале. Если включён UFW,
разрешите TCP 8765 от IP клиента по правилам своего стенда. На клиенте:

```sh
SERVER_IP=192.168.0.108
lansync init "$HOME/demo-client" --mode client --url "https://$SERVER_IP:8765"
```

Передайте **только ca.pem и token.txt** из серверного demo-client-bundle в
demo-client/credentials на клиенте, например через уже настроенный SSH/SCP.
Приватный ключ сервера остаётся на сервере. Проверка на клиенте:

```sh
lansync run "$HOME/demo-client/project.toml" --check
lansync inspect "$HOME/demo-client/project.toml"
lansync run "$HOME/demo-client/project.toml" --action doctor
```

При ошибке doctor исправьте адрес, доступность порта, сертификат или токен до
демонстрации. Сам показ начните с краткой схемы «сервер → HTTPS → клиент».
Откройте две папки shared рядом, а также терминалы сервера и клиента.

## 1. План и первый обмен

В другом терминале сервера создайте исходный файл:

```sh
printf 'Исходная версия\n' > "$HOME/demo-server/shared/заметки.txt"
```

На клиенте:

```sh
lansync run "$HOME/demo-client/project.toml" --action plan
lansync run "$HOME/demo-client/project.toml" --action once
cat "$HOME/demo-client/shared/заметки.txt"
sha256sum "$HOME/demo-client/shared/заметки.txt"
```

До once план не создаёт файл на клиенте. Сравните SHA-256 с такой же командой
на сервере. Для показа отдельного модуля индекса на сервере выполните:

```sh
lansync scan "$HOME/demo-server/shared" --state-dir "$HOME/demo-index"
```

Покажите demo-index/manifest.json: относительный путь, размер и SHA-256.
Это отдельный снимок для показа; сетевой сервер выдаёт свой манифест через API.
Объясните, что индекс и манифест — модуль преддипломной практики,
а сетевой обмен и согласование изменений — остальная часть дипломной системы.

## 2. Обратное направление

На клиенте:

```sh
printf 'Файл клиента\n' > "$HOME/demo-client/shared/от-клиента.txt"
lansync run "$HOME/demo-client/project.toml" --action once
```

Покажите новый файл в shared сервера. Пока используйте однократный режим,
чтобы следующую ситуацию конфликта можно было воспроизвести предсказуемо.

## 3. Конфликт без потери версий

Измените заметки.txt сначала на сервере, затем на клиенте, не запуская sync между ними:

```sh
# Сервер
printf 'Редакция сервера\n' > "$HOME/demo-server/shared/заметки.txt"
```

```sh
# Клиент
printf 'Редакция клиента\n' > "$HOME/demo-client/shared/заметки.txt"
lansync run "$HOME/demo-client/project.toml" --action once
lansync run "$HOME/demo-client/project.toml" --action status
lansync run "$HOME/demo-client/project.toml" --action resolve --path заметки.txt --choose both --copy-path заметки-клиента.txt
lansync run "$HOME/demo-client/project.toml" --action once
```

Первый once сообщает конфликт (код 2 — ожидаемый результат). После разрешения
заметки.txt содержит серверную редакцию, а заметки-клиента.txt — клиентскую;
следующий once передаёт копию на сервер. Покажите оба файла на обеих машинах.

## 4. Удаление и резервная копия

Удалите через файловый менеджер **тестовый** от-клиента.txt на клиенте. Затем:

```sh
lansync run "$HOME/demo-client/project.toml" --action plan
lansync run "$HOME/demo-client/project.toml" --action once
lansync run "$HOME/demo-client/project.toml" --action recovery-list --json
lansync run "$HOME/demo-client/project.toml" --action recovery-verify
```

Покажите удаление на сервере. В клиентском recovery уже должна быть копия заметок.txt,
заменённых при разрешении конфликта. Выберите её ID из списка и экспортируйте:

```sh
lansync run "$HOME/demo-client/project.toml" --action recovery-restore --id ID_ИЗ_СПИСКА --output "$HOME/demo-restored.txt"
```

Файл назначения должен быть новым. Это экспорт сохранённой версии, а не
автоматическая отмена всех изменений и не доказательство восстановления после аварии.

## 5. Фоновая работа и штатная остановка

На клиенте запустите `lansync run "$HOME/demo-client/project.toml"`.
Во втором клиентском терминале — `lansync run "$HOME/demo-client/project.toml" --action monitor`.
Создайте новый тестовый файл на клиенте и покажите его появление на сервере.
Изменение на сервере обнаруживается периодическим опросом: стандартный interval —
30 секунд. При необходимости заранее установите interval = 2 в клиентском TOML.

Остановите клиент Ctrl+C, затем покажите status: stopped и отсутствие блокировки.
Запустите его снова с тем же TOML — повторная настройка проекта не требуется.
Завершите клиент, монитор и сервер Ctrl+C. Не запускайте once одновременно
с непрерывным клиентом с тем же state_dir.

## Что приложить к отчёту

Версию LANSync на обоих узлах, схему сети, скриншоты плана/конфликта/двух версий,
результаты сравнения SHA-256 и итоговый статус. Токены и приватные ключи в отчёт
не включайте. Показ подтверждает выбранные операции на данном стенде; длительные
испытания, автозапуск после перезагрузки и аварийное восстановление — отдельные проверки.
