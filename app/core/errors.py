"""Uygulama hata türleri. `user_message` arayüzde, `detail` ayrıntılar bölümünde gösterilir."""


class MediaStudioError(Exception):
    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class DependencyMissingError(MediaStudioError):
    pass


class SettingsError(MediaStudioError):
    pass
