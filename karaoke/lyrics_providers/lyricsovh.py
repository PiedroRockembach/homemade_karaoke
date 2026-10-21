"""lyrics.ovh (ADR-0005 §1.1): `/suggest/<artista> <faixa>` (Deezer) para achar e conferir o nome, depois
`/v1/<artista>/<faixa>` com os nomes canônicos do item."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from karaoke.lyrics_providers import PlainLyrics, clean_text, network_error
from karaoke.lyrics_providers.http import make_client
from karaoke.lyrics_providers.vagalume import slug

log = logging.getLogger("karaoke.lyrics_providers")

BASE_URL = "https://api.lyrics.ovh"


class LyricsOvhProvider:
    name = "lyricsovh"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = make_client(transport)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> httpx.Response:
        try:
            return self._client.get(BASE_URL + path)
        except httpx.HTTPError as exc:
            raise network_error(exc) from None

    def search(self, track: str, artist: str | None) -> list[PlainLyrics]:
        from karaoke.lyrics_providers.chain import match

        term = f"{artist or ''} {track}".strip()
        if not term:
            return []
        r = self._get("/suggest/" + quote(term, safe=""))
        if r.status_code != 200:
            log.info("lyricsovh: suggest respondeu %s", r.status_code)
            return []
        try:
            items = r.json().get("data") or []
        except (ValueError, AttributeError):
            log.info("lyricsovh: suggest com JSON inesperado")
            return []
        item = next(
            (
                it
                for it in items
                if isinstance(it, dict)
                and isinstance(it.get("artist"), dict)
                and match(str(it.get("title") or ""), str(it["artist"].get("name") or ""), track, artist)
            ),
            None,
        )
        if item is None:
            log.info("lyricsovh: nenhuma sugestão com o mesmo nome")
            return []
        title, art = str(item["title"]), str(item["artist"]["name"])
        r = self._get(f"/v1/{quote(art, safe='')}/{quote(title, safe='')}")
        if r.status_code != 200:
            log.info("lyricsovh: letra respondeu %s", r.status_code)
            return []
        try:
            text = r.json().get("lyrics")
        except (ValueError, AttributeError):
            text = None
        lines = clean_text(text) if isinstance(text, str) else []
        if not lines:
            log.info("lyricsovh: letra vazia")
            return []
        dur = item.get("duration")
        return [
            PlainLyrics(
                provider=self.name,
                provider_id=f"lyricsovh:{item['id']}"
                if item.get("id")
                else f"lyricsovh:{slug(art)}/{slug(title)}",
                track_name=title,
                artist_name=art,
                duration_ms=int(dur) * 1000 if isinstance(dur, int | float) and dur > 0 else None,
                text_lines=lines,
            )
        ]
