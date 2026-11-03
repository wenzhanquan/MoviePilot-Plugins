"""Compatibility for this MoviePilot image's Telegram client dependencies."""

def ensure_urllib3_header_param_compat() -> None:
    try:
        from urllib3 import fields
    except ImportError:
        return
    if not hasattr(fields, 'format_header_param'):
        for name in ('format_header_param_html5', 'format_multipart_header_param', 'format_header_param_rfc2231'):
            fallback = getattr(fields, name, None)
            if fallback is not None:
                fields.format_header_param = fallback
                break
    _configure_telegram_tls()


def _configure_telegram_tls() -> None:
    import ssl
    import threading
    import requests
    from telebot import apihelper

    if apihelper.CUSTOM_REQUEST_SENDER is not None:
        return

    class TelegramTLSAdapter(requests.adapters.HTTPAdapter):
        def __init__(self):
            self._telegram_ssl_context = ssl.create_default_context()
            super().__init__()

        def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
            kwargs['ssl_context'] = self._telegram_ssl_context
            return super().init_poolmanager(connections, maxsize, block=block, **kwargs)

        def proxy_manager_for(self, proxy, **kwargs):
            kwargs['ssl_context'] = self._telegram_ssl_context
            return super().proxy_manager_for(proxy, **kwargs)

    local = threading.local()

    def telegram_request(method, request_url, **kwargs):
        if not hasattr(local, 'session'):
            session = requests.Session()
            session.mount('https://api.telegram.org/', TelegramTLSAdapter())
            local.session = session
        return local.session.request(method, request_url, **kwargs)

    apihelper.CUSTOM_REQUEST_SENDER = telegram_request
