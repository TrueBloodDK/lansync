"""Actionable explanations; original diagnostic detail stays in the journal."""


def explain(message):
    value = str(message).lower()
    if any(word in value for word in ('pending_replacements', 'unfinished file replacement', 'pending records')):
        return 'Обнаружена незавершённая замена файла. Остановите другие клиенты и проверьте recovery по инструкции восстановления; не удаляйте служебные файлы.'
    if 'sync.lock' in value or 'state is locked' in value:
        return 'Состояние проекта занято или осталось заблокированным после аварии. Дождитесь остановки другого клиента. Если он уже завершён, проверьте восстановление; не удаляйте блокировку вслепую.'
    if any(word in value for word in ('certificate_verify_failed', 'certificate verify failed', 'certificate_verification_failed')):
        return 'Не удалось проверить сертификат сервера. Проверьте адрес сервера, время на устройствах и сертификат CA в комплекте клиента.'
    if any(word in value for word in ('authorization_failed', 'unauthorized')):
        return 'Сервер отклонил токен. Проверьте, что используется актуальный комплект реквизитов этого сервера.'
    if any(word in value for word in ('access_denied', 'forbidden')):
        return 'Сервер запретил операцию. Проверьте права доступа и разрешение записи на сервере.'
    if any(word in value for word in ('no space left', 'disk full', 'winerror 112')):
        return 'Не хватает места на диске. Освободите место на соответствующем устройстве и повторите операцию.'
    if any(word in value for word in ('permission denied', 'access is denied', 'winerror 5')):
        return 'Нет доступа к файлу или папке. Проверьте права текущего пользователя и не занят ли файл другим приложением.'
    if any(word in value for word in ('timeout', 'timed out')):
        return 'Сервер не ответил вовремя. Проверьте сеть и состояние сервера; при медленной сети проверьте тайм-аут в настройках.'
    if any(word in value for word in ('connection_failed', 'connect', 'network', 'name resolution', 'getaddrinfo', 'name or service', 'winerror 10061')):
        return 'Не удалось связаться с сервером. Проверьте его запуск, адрес, соединение с локальной сетью и правило брандмауэра для порта сервера.'
    if any(word in value for word in ('invalid_configuration', 'client_tls_configuration', 'transport_configuration', 'credentials', 'token', 'ca.pem')):
        return 'Проверьте конфигурацию проекта и доступность файлов реквизитов. Комплект клиента должен содержать корректные ca.pem и token.txt.'
    if 'invalid_manifest' in value:
        return 'Ответ сервера не соответствует ожидаемому формату. Проверьте совместимость версий и журнал сервера.'
    return 'Операция не выполнена. Подробности приведены в журнале; сохраните его для разбора ошибки.'
