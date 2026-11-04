# Работа с проектом LANSync

Один client.toml описывает подключение и локальную папку. Создайте shared,
подготовьте сертификаты по NETWORK.md и скопируйте examples/client.toml рядом.
Укажите IP сервера из сертификата. Файлы состояния и credentials держите вне shared.

## Подготовка

1. `lansync run client.toml --check` — проверка структуры и параметров.
2. `lansync run client.toml --action doctor` — проверка доступа к серверу.
3. `lansync run client.toml --action plan` — предварительный просмотр.
4. `lansync run client.toml --action once` — применить один проход.
5. `lansync run client.toml` — работать в режиме из файла, например watch=true.

Для плана и диагностики доступен --json. Действие once всегда применяет изменения,
даже если исходная конфигурация содержит dry_run=true. Дополнительные действия
не наследуют log_file: запуск основного режима продолжает использовать его.

## Конфликты

Остановите непрерывный клиент Ctrl+C. Выполните once, просмотрите конфликтные
версии и выберите нужную:

```sh
lansync run client.toml --action status
lansync run client.toml --action resolve --path notes.txt --choose local
```

Для принятия серверной версии используйте --choose remote. Новые изменения после
сохранения снимка конфликта требуют повторной сверки. Не удаляйте baseline.json.

## Резервные копии

```sh
lansync run client.toml --action recovery-list --json
lansync run client.toml --action recovery-verify
lansync run client.toml --action recovery-restore --id ID --output ./restored.txt
```

Выберите ID из списка. output относится к текущему рабочему каталогу терминала;
пути внутри TOML — к каталогу TOML. Существующий output не перезаписывается.
Для серверных копий используйте server.toml с явным state_dir. Проверку целостности
выполняйте после остановки процесса, создающего копии.

## Для будущего интерфейса

config.load_project(path) возвращает ProjectConfig без argparse и без сетевых
операций. values содержит типизированные пути и значения по умолчанию.
namespace() создаёт объект параметров для общих проверок. arguments() — адаптер
для существующего CLI. Конфигурацию после загрузки следует считать неизменяемой.
Схема проверяет неизвестные ключи и обязательные параметры; validate_options
проверяет сочетания значений. workflow.action_arguments выбирает действие.

Функции sync.synchronize, sync.resolve_conflict, diagnostics.diagnose,
status.read_status и recovery.list_copies/verify_copies/restore_copy доступны
отдельно от терминального вывода. GUI должен выполнять длительные операции вне
своего потока интерфейса. CLI остаётся адаптером; схема и его аргументы пока
определены отдельно, поэтому при добавлении параметров нужно обновлять оба места.
Полное устранение этой дубликации и замена повторного CLI-разбора прямым диспетчером
не являются завершёнными частями текущего рефакторинга.
