# Установка и запуск LANSync

## Установка пакета

Требуется Python 3.11+. Создайте окружение вне исходников:

```sh
python -m venv lansync-env
```

Linux: `lansync-env/bin/python -m pip install /path/to/lansync-0.1.0-py3-none-any.whl`.
Windows: `lansync-env\Scripts\python.exe -m pip install C:\path\lansync-0.1.0-py3-none-any.whl`.
Зависимости устанавливаются из индекса pip. Это Python-пакет, не автономный бинарник.
Далее используйте lansync из bin (Linux) или Scripts (Windows) этого окружения.
Для обновления остановите процессы и установите новый wheel с `pip install --upgrade --force-reinstall`.
Перед обновлением сохраните конфигурации, состояние и recovery.

## Новый проект

```sh
lansync init ./server-project --mode server
lansync init ./client-project --mode client --url https://192.168.0.10:8765
```

Родительский каталог должен существовать, каталог проекта должен быть новым.
Создаются project.toml, shared и credentials. Клиент включает наблюдение за
локальными событиями; сервер разрешает изменения и удаления по общему токену.
Команда не открывает firewall. Для создания сертификатов установите extra setup:
`python -m pip install "lansync[setup]"`; для локального wheel добавьте [setup]
к пути файла в команде pip install. Затем:

```sh
lansync credentials create --host 192.168.0.10 --output-dir ./server-project/credentials
lansync credentials export-client --source-dir ./server-project/credentials --output-dir ./client-bundle
```

Передайте ca.pem и token.txt из client-bundle в client-project/credentials
доверенным способом. Каталог назначения должен быть новым или пустым.
Для нескольких адресов повторяйте --host. Сертификаты предназначены для частной
LAN-сети; клиент доверяет переданному CA. Ключ CA не сохраняется: для новых
адресов создайте новый комплект и обновите CA на клиентах. Секреты не печатаются.
На POSIX файлы создаются с правами 0600, новый каталог — 0700; на Windows
действуют унаследованные разрешения профиля пользователя. Существующий пустой
каталог сохраняет свои права. Ошибка записи может оставить частичный комплект;
не используйте его и подготовьте новый пустой каталог после проверки.
Приватный ключ клиенту не передавайте. IP в клиентском URL должен входить в сертификат.

```sh
lansync run ./client-project/project.toml --check
lansync run ./client-project/project.toml --action doctor
lansync run ./client-project/project.toml --action plan
lansync run ./client-project/project.toml
```

## Linux: пользовательская служба systemd

Если система использует systemd, создайте ~/.config/systemd/user/lansync.service,
подставив абсолютные пути. Сначала проверьте тот же запуск вручную.

```ini
[Unit]
Description=LANSync synchronization

[Service]
Type=simple
ExecStart=/home/USER/lansync-env/bin/lansync run /home/USER/client-project/project.toml
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=90

[Install]
WantedBy=default.target
```

Команды: `systemctl --user daemon-reload`, `systemctl --user enable --now lansync`,
`systemctl --user status lansync`, `journalctl --user -u lansync -f`.
Остановка: `systemctl --user stop lansync`.
Пользовательская служба обычно связана с сеансом пользователя; запуск без входа
в систему требует отдельной настройки администратора. Не включайте log_file
с постоянным именем: при перезапуске существующий файл отклоняется. Используйте journal.
После принудительной остановки проверьте sync.lock и pending-записи по SYNC.md.

## Windows: запуск при входе

В Планировщике заданий создайте задачу для своего пользователя с триггером
«При входе в систему». Программа — абсолютный путь к lansync-env\Scripts\lansync.exe.
Аргументы: `run "C:\Users\USER\client-project\project.toml"`.
Рабочий каталог — папка проекта. В параметрах выберите «Не запускать новый экземпляр»,
отключите ограничение общей длительности задачи. Для первого испытания выберите
запуск только при входе пользователя. Повышенные права приложению не требуются.
Для фонового запуска без консольного окна можно выбрать pythonw.exe из окружения
и аргументы `-m lansync run "...\project.toml"`; состояние проверяется через status.
Остановка задачи может быть принудительной: перед следующим запуском проверьте
блокировку по SYNC.md. Не запускайте одновременно ручной sync с тем же состоянием.
Это инструкция настройки задания, не установленная Windows-служба.

## Проверка выпуска разработчиком

```sh
python -m pip install build
python scripts/release_check.py --output ./release-new
```

Создаются wheel и sdist, затем отдельное чистое venv. Wheel устанавливается
с зависимостями, выполняется pip check, запуск CLI, init, проверка TOML и scan.
Все smoke-команды выполняются вне репозитория без PYTHONPATH. Требуется интернет
для сборочных зависимостей и установки пакетов. Проверка не заменяет сетевые
испытания и полный набор unittest. Каталог output должен быть новым.
