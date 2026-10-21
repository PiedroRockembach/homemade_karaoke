"""Vagalume (ADR-0005 §1.1): API oficial se houver chave; senão (ou se ela falhar) a página pública por slug.

A URL da API leva a chave: nunca logar URL/params aqui — só nome do provedor + status/classe da exceção.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

import httpx

from karaoke.lyrics.titleclean import normalize, strip_noise
from karaoke.lyrics_providers import PlainLyrics, clean_text, network_error
from karaoke.lyrics_providers.http import make_client

log = logging.getLogger("karaoke.lyrics_providers")

API_URL = "https://api.vagalume.com.br/search.php"
PAGE_URL = "https://www.vagalume.com.br/{art}/{mus}.html"
MIN_LINES = 2


def slug(s: str) -> str:
    """Ex.: "Banda Ação" → "banda-acao"; "&" vira "e" antes da normalização."""
    s = re.sub(r"['\u2019`]", "", s or "")  # apóstrofo junta a palavra: "Don't" → "dont"
    return normalize(s.replace("&", " e ")).replace(" ", "-")


class _PageParser(HTMLParser):
    """Extrai `div#lyrics` (com `<br>` → quebra de linha), o 1º `<h1>` sem filhos, o `<a>` do `<h1 class~=darkBG>`
    e `og:title`. Entidades já vêm convertidas (`convert_charrefs`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lyrics: list[str] | None = None
        self._lyr_depth = 0  # >0 = dentro do div#lyrics (profundidade de divs)
        self._lyr_done = False
        self._skip = 0  # >0 = dentro de <script>/<style> no div#lyrics
        self.track: str | None = None
        self.artist: str | None = None
        self.og_title: str | None = None
        self._h1: dict | None = None  # {"dark": bool, "text": [...], "children": bool, "a": [...] | None}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if self._lyr_depth:
            if tag == "div":
                self._lyr_depth += 1
            elif tag in ("script", "style"):
                self._skip += 1
            elif tag == "br" and not self._skip:
                self.lyrics.append("\n")  # type: ignore[union-attr]
            return
        if tag == "div" and a.get("id") == "lyrics" and not self._lyr_done:
            self.lyrics, self._lyr_depth = [], 1
        elif tag == "meta" and a.get("property") == "og:title" and self.og_title is None:
            self.og_title = a.get("content") or None
        elif tag == "h1":
            self._h1 = {
                "dark": "darkBG" in (a.get("class") or "").split(),
                "text": [],
                "kids": False,
                "a": None,
            }
        elif self._h1 is not None:
            self._h1["kids"] = True
            if tag == "a" and self._h1["a"] is None:
                self._h1["a"] = []

    def handle_endtag(self, tag: str) -> None:
        if self._lyr_depth:
            if tag in ("script", "style"):
                self._skip = max(0, self._skip - 1)
            elif tag == "div":
                self._lyr_depth -= 1
                if not self._lyr_depth:
                    self._lyr_done = True
            return
        h = self._h1
        if h is None:
            return
        if tag == "a" and isinstance(h["a"], list) and h.get("a_done") is None:
            h["a_done"] = True
        elif tag == "h1":
            text = " ".join("".join(h["text"]).split())
            if not h["kids"] and text and self.track is None:
                self.track = text
            if h["dark"] and h["a"] and self.artist is None:
                self.artist = " ".join("".join(h["a"]).split()) or None
            self._h1 = None

    def handle_data(self, data: str) -> None:
        if self._lyr_depth:
            if not self._skip:
                self.lyrics.append(data)  # type: ignore[union-attr]
            return
        h = self._h1
        if h is not None:
            h["text"].append(data)
            if isinstance(h["a"], list) and h.get("a_done") is None:
                h["a"].append(data)


def parse_page(html: str) -> tuple[str | None, str | None, list[str]]:
    """HTML da página → (faixa, artista, linhas limpas). Linhas vazias = HTML mudou/sem letra."""
    p = _PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — HTML malformado = "HTML mudou"
        return None, None, []
    track, artist = p.track, p.artist
    if (not track or not artist) and p.og_title:
        parts = [s.strip() for s in p.og_title.split(" - ")]
        if len(parts) >= 3 and parts[-1].upper() == "VAGALUME":
            track = track or " - ".join(parts[:-2])
            artist = artist or parts[-2]
    lines = clean_text("".join(p.lyrics)) if p.lyrics is not None else []
    return track, artist, lines


class VagalumeProvider:
    name = "vagalume"

    def __init__(self, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self._key = (api_key or "").strip() or None
        self._client = make_client(transport)

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            return self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise network_error(exc) from None  # sem encadear: a mensagem do httpx traz a URL (com a chave)

    def search(self, track: str, artist: str | None) -> list[PlainLyrics]:
        from karaoke.lyrics_providers.chain import match

        if self._key and artist:
            found = self._api(track, artist)
            if found is not None and match(found.track_name, found.artist_name, track, artist):
                return [found]
            if found is not None:
                log.info("vagalume: API devolveu outra música; tentando a página")
        return self._page(track, artist)

    def _api(self, track: str, artist: str) -> PlainLyrics | None:
        r = self._get(API_URL, {"art": artist, "mus": track, "apikey": self._key or ""})
        if r.status_code != 200:
            log.info("vagalume: API respondeu %s; tentando a página", r.status_code)
            return None
        try:
            d = r.json()
            mus = d["mus"][0]
            if d.get("type") not in ("exact", "aprox") or not isinstance(mus.get("text"), str):
                raise ValueError(d.get("type"))
            lines = clean_text(mus["text"])
            if not lines:
                raise ValueError("texto vazio")
            return PlainLyrics(
                provider=self.name,
                provider_id=f"vagalume:api:{mus['id']}"
                if mus.get("id")
                else f"vagalume:{slug(artist)}/{slug(track)}",
                track_name=str(mus.get("name") or track),
                artist_name=str((d.get("art") or {}).get("name") or artist),
                duration_ms=None,
                text_lines=lines,
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            log.info("vagalume: API sem resultado (%s); tentando a página", type(exc).__name__)
            return None

    def _page(self, track: str, artist: str | None) -> list[PlainLyrics]:
        from karaoke.lyrics_providers.chain import match

        art, mus = slug(artist or ""), slug(strip_noise(track))
        if not art or not mus:
            return []  # sem artista não há busca pública
        r = self._get(PAGE_URL.format(art=art, mus=mus))
        if r.status_code != 200:
            log.info("vagalume: página respondeu %s", r.status_code)
            return []
        name, artist_name, lines = parse_page(r.text)
        if len(lines) < MIN_LINES or not name:
            log.info("vagalume: página sem letra reconhecível (HTML mudou?)")
            return []
        if not match(name, artist_name or "", track, artist):
            log.info("vagalume: página é de outra música")
            return []
        return [
            PlainLyrics(
                provider=self.name,
                provider_id=f"vagalume:{art}/{mus}",
                track_name=name,
                artist_name=artist_name or artist or "",
                duration_ms=None,
                text_lines=lines,
            )
        ]


__all__ = ["VagalumeProvider", "parse_page", "slug"]
