"""Загрузка медиа в MAX с кэшем переиспользуемых токенов."""
import os
from typing import Dict, Tuple


class Uploader:
    def __init__(self, client):
        self._client = client
        self._cache: Dict[Tuple[str, int, int], str] = {}

    async def upload(self, path: str, kind: str) -> str:
        """kind ∈ image|video|audio|file. Возвращает токен вложения."""
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
        token = self._cache.get(key)
        if token:
            return token
        url = await self._client.get_upload_url(kind)
        token = await self._client.upload_to_url(url, path)
        self._cache[key] = token
        return token

    def clear(self) -> None:
        self._cache.clear()
