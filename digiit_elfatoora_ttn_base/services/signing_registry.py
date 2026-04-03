import logging

_logger = logging.getLogger(__name__)


class SigningProviderRegistry:
    """Simple in-process registry for signing providers.

    Provider addons register at import-time so the base wizard can delegate
    without hardcoding provider implementations.
    """

    _providers = {}

    @classmethod
    def register(cls, provider_cls):
        code = getattr(provider_cls, "code", None)
        label = getattr(provider_cls, "label", None)
        if not code or not label:
            raise ValueError("Signing provider must define `code` and `label`.")
        if code in cls._providers and cls._providers[code] is not provider_cls:
            _logger.warning("Signing provider code %s re-registered (%s -> %s)", code, cls._providers[code], provider_cls)
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def codes(cls):
        return list(cls._providers.keys())

    @classmethod
    def selection(cls):
        """Return Odoo selection list [(code, label), ...]."""
        return [(p.code, p.label) for p in cls._providers.values()]

    @classmethod
    def get(cls, code):
        provider_cls = cls._providers.get(code)
        if not provider_cls:
            return None
        return provider_cls

