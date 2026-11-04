class TelegramError(RuntimeError):
    """Safe Telegram diagnostics: never contains request URLs or credentials."""

    def __init__(self, method, code=None, reason='unknown'):
        self.method, self.code, self.reason = method, code, reason
        super().__init__(f'Telegram {method}: status={code}, reason={reason}')
