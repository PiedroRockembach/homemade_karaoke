"""Alinhamento "pela voz": texto de uma letra (LRCLIB/colada) + tempos das palavras do Whisper sobre o vocal.

Entrada: linhas de uma candidata (contrato lyrics-format, com `start_ms` do LRC) e os segmentos crus do
faster-whisper (`word_timestamps=True`, tempos em segundos). Saída: linhas com tempos de linha **e palavra**
(origem `aligned`), já com as invariantes do contrato (§2) garantidas por `builder._finalize`.

Algoritmo (sem dependências novas):
1. Normaliza palavras dos dois lados (minúsculas, sem acento/pontuação; `'cause` → `cause`).
2. Corrige palavras do Whisper com início absurdo (1ª palavra de um segmento depois do VAD costuma "começar"
   segundos antes de ser cantada): duração > `MAX_WORD_MS` → início = fim − estimativa por caractere.
3. Needleman-Wunsch global (palavras da letra × palavras do Whisper) com similaridade difusa
   (`difflib.SequenceMatcher`), em duas passadas: a 1ª só por texto estima o offset global (mediana de
   `t_whisper − t_lrc`); a 2ª penaliza casar palavras muito longe de `t_lrc + offset` (desempata refrões
   repetidos).
4. Linha "ancorada" = palavras casadas suficientes (≥ 50 % ou ≥ 2) e tempo coerente (monotônico).
   Palavras não casadas dentro de uma linha ancorada são interpoladas por caractere entre vizinhas.
5. Linhas não ancoradas: interpolação linear dos tempos do LRC entre as âncoras vizinhas; nas pontas, tempo do
   LRC + offset estimado (último recurso: LRC + 0).

Dono: backend-engineer (T-001, ciclo 20261004-1742).
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from karaoke.lyrics import builder

MAX_WORD_MS = 2000  # palavra cantada mais longa aceita como veio do Whisper
CHAR_MS = 90  # estimativa de duração por caractere (início corrigido / palavras não casadas)
MIN_WORD_MS = 120
MATCH_SIM = 0.72  # similaridade mínima para considerar "a mesma palavra"
GAP = -0.6
MISMATCH = -1.0
TIME_TOLERANCE_MS = 4000  # sem penalidade de tempo até aqui (deriva/versões levemente diferentes)
TIME_PENALTY_SPAN_MS = 20000  # penalidade cresce até 1.5 pontos em +20 s
MAX_LINE_SPAN_MS = 20000  # linha cujas palavras casadas se espalham mais que isso é suspeita
INNER_GAP_MS = 700  # buraco entre palavras do mesmo segmento do Whisper que denuncia início errado
SILENCE_DB = -30  # abaixo disso (relativo ao p95 do vocal) é silêncio
RISE_DB = 12  # subida de energia que marca o ataque de uma palavra depois de respiração/eco
ENVELOPE_HOP_MS = 10

_APOS = re.compile(r"[’'`´]")
_NON_WORD = re.compile(r"[^0-9a-z]+")


def norm_word(text: str) -> str:
    """Minúsculas, sem acento nem pontuação (para comparar palavras)."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _APOS.sub("", t)
    return _NON_WORD.sub("", t)


@dataclass
class WWord:
    """Palavra do Whisper (ms)."""

    text: str
    norm: str
    start_ms: int
    end_ms: int
    prob: float = 1.0


@dataclass
class LWord:
    """Palavra da letra."""

    text: str
    norm: str
    line: int
    pos: int  # índice da palavra na linha
    lrc_ms: int  # tempo de referência no LRC (início da linha, ou da palavra se Enhanced LRC)


@dataclass
class AlignStats:
    lines: int = 0
    anchored_lines: int = 0
    interpolated_lines: int = 0
    fallback_lines: int = 0  # LRC + offset (sem âncora nenhuma)
    words: int = 0
    matched_words: int = 0
    offset_ms: int | None = None  # mediana de (início alinhado − início LRC) nas linhas ancoradas
    drift_ms_per_min: float | None = None  # inclinação de (alinhado − LRC) ao longo da música
    per_line: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return round(self.anchored_lines / self.lines, 3) if self.lines else 0.0

    def as_meta(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "anchored_lines": self.anchored_lines,
            "interpolated_lines": self.interpolated_lines,
            "fallback_lines": self.fallback_lines,
            "matched_words": self.matched_words,
            "words": self.words,
            "coverage": self.coverage,
            "offset_ms": self.offset_ms,
            "drift_ms_per_min": self.drift_ms_per_min,
        }


# ---- envelope de energia do vocal -------------------------------------------------------------------
@dataclass
class Envelope:
    """Energia do vocal separado em dB relativos ao percentil 95 (0 = forte), um valor a cada `hop_ms`."""

    hop_ms: int
    db: list[int]
    threshold_db: int = SILENCE_DB

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Envelope | None:
        if not d or not d.get("db") or not d.get("hop_ms"):
            return None
        return cls(int(d["hop_ms"]), [int(x) for x in d["db"]])

    def _idx(self, t_ms: int) -> int:
        return max(0, min(len(self.db), t_ms // self.hop_ms))

    def voiced_ratio(self, lo: int, hi: int) -> float:
        a, b = self._idx(lo), self._idx(hi)
        if b <= a:
            return 0.0
        return sum(1 for x in self.db[a:b] if x > self.threshold_db) / (b - a)

    def first_voiced(self, lo: int, hi: int, min_run_ms: int = 50) -> int | None:
        """Início do 1º trecho com voz (≥ `min_run_ms`) em [lo, hi)."""
        need = max(1, min_run_ms // self.hop_ms)
        run = 0
        for k in range(self._idx(lo), self._idx(hi)):
            run = run + 1 if self.db[k] > self.threshold_db else 0
            if run >= need:
                return (k - need + 1) * self.hop_ms
        return None

    def last_onset(self, lo: int, hi: int, min_silence_ms: int = 150) -> int | None:
        """Último ponto em [lo, hi) onde a voz começa depois de ≥ `min_silence_ms` de silêncio."""
        need = max(1, min_silence_ms // self.hop_ms)
        a, b = self._idx(lo), self._idx(hi)
        for k in range(b - 1, max(a, need) - 1, -1):
            if self.db[k] > self.threshold_db and all(x <= self.threshold_db for x in self.db[k - need : k]):
                return k * self.hop_ms
        return None

    def rises(self, lo: int, hi: int, jump_db: int = RISE_DB, window_ms: int = 100) -> list[tuple[int, int]]:
        """Ataques em [lo, hi): energia sobe ≥ `jump_db` dentro de `window_ms` a partir de um vale (≤ -14 dB)
        até um nível de voz (≥ -12 dB). Retorna [(instante logo após o vale, tamanho da subida em dB)]."""
        w = max(2, window_ms // self.hop_ms)
        a, b = max(1, self._idx(lo)), self._idx(hi)
        out: list[tuple[int, int]] = []
        last_k = -(10**9)
        for k in range(a, b):
            v = self.db[k]
            if v < -12 or k - last_k < w:
                continue
            lo_k = max(0, k - w)
            win = self.db[lo_k:k]
            m = min(win)
            if m <= -14 and v - m >= jump_db:
                j = lo_k + max(i for i, x in enumerate(win) if x == m)
                out.append(((j + 1) * self.hop_ms, v - m))
                last_k = k
        return out

    def best_rise(self, lo: int, hi: int) -> int | None:
        """O ataque mais forte em [lo, hi) (empate: o mais tardio)."""
        rs = self.rises(lo, hi)
        if not rs:
            return None
        return max(rs, key=lambda r: (r[1], r[0]))[0]

    def last_rise(self, lo: int, hi: int) -> int | None:
        rs = self.rises(lo, hi)
        return rs[-1][0] if rs else None


def envelope_from_samples(samples: Any, sample_rate: int, hop_ms: int = ENVELOPE_HOP_MS) -> dict[str, Any]:
    """RMS por janela (numpy) → dB relativos ao percentil 95, arredondados (int ≥ -99). Roda no worker."""
    import numpy as np

    hop = max(1, sample_rate * hop_ms // 1000)
    n = len(samples) // hop
    if n == 0:
        return {"hop_ms": hop_ms, "db": []}
    frames = np.asarray(samples[: n * hop], dtype=np.float32).reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1) + 1e-12))
    ref = float(np.percentile(db, 95))
    rel = np.clip(np.round(db - ref), -99, 10).astype(int)
    return {"hop_ms": hop_ms, "db": rel.tolist()}


# ---- entrada -------------------------------------------------------------------------------------
def _est_ms(norm: str) -> int:
    return max(MIN_WORD_MS, min(MAX_WORD_MS, len(norm) * CHAR_MS + 120))


def whisper_words(segments: list[dict[str, Any]], envelope: Envelope | None = None) -> list[WWord]:
    """Palavras do Whisper em ms, ordenadas, com o início corrigido onde o Whisper costuma errar.

    O Whisper cola as palavras de um segmento umas nas outras: o silêncio antes de uma palavra vira parte dela,
    e a 1ª palavra de um segmento frequentemente "começa" segundos antes de ser cantada. Correções:
    1. palavra seguida de um buraco > `INNER_GAP_MS` até a próxima do **mesmo** segmento: na prática é cantada
       logo antes da próxima → vai para o último ataque de voz antes dela (ou próxima − estimativa);
    2. palavra que começa em silêncio (com envelope): início vai para o 1º trecho com voz dentro dela;
    3. sem envelope, 1ª palavra de segmento com duração > `MAX_WORD_MS`: início = fim − estimativa.
    """
    out: list[WWord] = []
    for seg in segments:
        raw: list[WWord] = []
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            norm = norm_word(text)
            if not norm or w.get("start") is None or w.get("end") is None:
                continue
            s, e = round(float(w["start"]) * 1000), round(float(w["end"]) * 1000)
            raw.append(WWord(text, norm, s, max(e, s + 1), float(w.get("probability") or 1.0)))
        for k, w in enumerate(raw):
            nxt = raw[k + 1] if k + 1 < len(raw) else None
            est = _est_ms(w.norm)
            if nxt is not None and nxt.start_ms - w.end_ms > INNER_GAP_MS:
                onset = None
                if envelope is not None:
                    onset = envelope.best_rise(w.start_ms + 40, nxt.start_ms - 40)
                    if onset is None:
                        onset = envelope.last_onset(w.start_ms, nxt.start_ms)
                    if onset is not None and onset > w.start_ms + 150:
                        w.start_ms = onset
                        w.end_ms = max(w.end_ms, min(nxt.start_ms, onset + est))
                else:
                    w.start_ms = max(w.start_ms, nxt.start_ms - est)
                    w.end_ms = nxt.start_ms
            elif envelope is not None:
                if envelope.voiced_ratio(w.start_ms, min(w.end_ms, w.start_ms + 100)) == 0.0:
                    v = envelope.first_voiced(w.start_ms, w.end_ms)
                    if v is not None and v < w.end_ms - MIN_WORD_MS // 2:
                        w.start_ms = v
                snapped = False
                if k == 0:
                    # 1ª palavra do segmento começando tarde: ataque de voz logo antes, sem silêncio no meio
                    prev_end = out[-1].end_ms if out else 0
                    o = envelope.last_onset(max(prev_end, w.start_ms - 1500), w.start_ms + 1)
                    if o is not None and o < w.start_ms - 150 and envelope.voiced_ratio(o, w.start_ms) > 0.9:
                        w.start_ms, snapped = o, True
                if not snapped and w.end_ms - w.start_ms > 1.5 * est:
                    # palavra longa demais: provavelmente absorveu respiração/eco antes do ataque real
                    r = envelope.best_rise(w.start_ms + 40, w.end_ms - 40)
                    if r is not None and w.end_ms - r >= MIN_WORD_MS // 2:
                        w.start_ms = r
                    elif k == 0 and w.end_ms - w.start_ms > 3 * est:
                        w.start_ms = w.end_ms - round(1.5 * est)
            elif k == 0 and w.end_ms - w.start_ms > MAX_WORD_MS:
                w.start_ms = w.end_ms - est
            out.append(w)
    out.sort(key=lambda w: w.start_ms)
    return out


def lyric_words(lines: list[dict[str, Any]]) -> list[LWord]:
    out: list[LWord] = []
    for li, ln in enumerate(lines):
        if ln.get("words"):
            toks = [(w["text"], int(w["start_ms"])) for w in ln["words"]]
        else:
            toks = [(t, int(ln["start_ms"])) for t in ln["text"].split()]
        for pos, (text, t) in enumerate(toks):
            n = norm_word(text)
            if n:
                out.append(LWord(text, n, li, pos, t))
    return out


# ---- alinhamento de sequência ------------------------------------------------------------------------
def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) <= 2:  # palavras curtas: só iguais
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def needleman_wunsch(
    lw: list[LWord], ww: list[WWord], offset_ms: int | None = None
) -> list[tuple[int, int, float]]:
    """Alinhamento global. Retorna pares (i da letra, j do Whisper, similaridade) casados (sim ≥ MATCH_SIM).

    Com `offset_ms`, casar palavras longe de `lrc_ms + offset` custa até 1.5 ponto (desempata repetições).
    """
    n, m = len(lw), len(ww)
    if n == 0 or m == 0:
        return []
    # cache de similaridade por par de normas (vocabulário pequeno)
    sim_cache: dict[tuple[str, str], float] = {}
    neg = float("-inf")
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 0 diag, 1 up (gap no whisper), 2 left (gap na letra)
    for i in range(1, n + 1):
        score[i][0] = GAP * i
        back[i][0] = 1
    for j in range(1, m + 1):
        score[0][j] = GAP * j * 0.5  # palavras do Whisper antes da letra (intro falada etc.) custam menos
        back[0][j] = 2
    for i in range(1, n + 1):
        a = lw[i - 1]
        row, prev = score[i], score[i - 1]
        brow = back[i]
        last_line = i == n
        for j in range(1, m + 1):
            b = ww[j - 1]
            key = (a.norm, b.norm)
            sim = sim_cache.get(key)
            if sim is None:
                sim = sim_cache[key] = similarity(a.norm, b.norm)
            if sim >= MATCH_SIM:
                s = 2.0 * sim
                if offset_ms is not None:
                    dt = abs(b.start_ms - (a.lrc_ms + offset_ms))
                    if dt > TIME_TOLERANCE_MS:
                        s -= 1.5 * min(1.0, (dt - TIME_TOLERANCE_MS) / TIME_PENALTY_SPAN_MS)
            else:
                s = MISMATCH
            diag = prev[j - 1] + s
            up = prev[j] + GAP
            # Whisper sobrando depois do fim da letra também custa menos (refrão extra, ad-libs finais)
            left = row[j - 1] + (GAP * 0.5 if last_line else GAP)
            best, arrow = diag, 0
            if up > best:
                best, arrow = up, 1
            if left > best:
                best, arrow = left, 2
            row[j] = best if best != neg else 0.0
            brow[j] = arrow
    pairs: list[tuple[int, int, float]] = []
    i, j = n, m
    while i > 0 and j > 0:
        arrow = back[i][j]
        if arrow == 0:
            sim = sim_cache.get((lw[i - 1].norm, ww[j - 1].norm), 0.0)
            if sim >= MATCH_SIM:
                pairs.append((i - 1, j - 1, sim))
            i, j = i - 1, j - 1
        elif arrow == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _median_offset(lw: list[LWord], ww: list[WWord], pairs: list[tuple[int, int, float]]) -> int | None:
    """Mediana de (t_whisper − t_lrc) nas 1ªs palavras de linha casadas (ou em todas, se poucas)."""
    firsts = [ww[j].start_ms - lw[i].lrc_ms for i, j, _ in pairs if lw[i].pos == 0]
    if len(firsts) < 3:
        firsts = [ww[j].start_ms - lw[i].lrc_ms for i, j, _ in pairs]
    return round(statistics.median(firsts)) if firsts else None


# ---- montagem das linhas ---------------------------------------------------------------------------------
def _distribute(texts: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Divide [start, end] entre palavras proporcionalmente ao nº de caracteres (+1 por espaço)."""
    if not texts:
        return []
    weights = [len(t) + 1 for t in texts]
    total = sum(weights)
    span = max(end - start, len(texts))
    out, acc = [], 0
    for w in weights:
        s = start + span * acc // total
        acc += w
        e = start + span * acc // total
        out.append((s, max(e, s + 1)))
    return out


def _line_tokens(line: dict[str, Any]) -> list[str]:
    if line.get("words"):
        return [w["text"] for w in line["words"]]
    return line["text"].split()


def _anchored_words(
    tokens: list[str], timed: dict[int, tuple[int, int]], envelope: Envelope | None = None
) -> list[dict[str, Any]]:
    """Palavras da linha com tempos: casadas vêm do Whisper; o resto é interpolado entre vizinhas.

    Palavras iniciais não casadas vão antes da 1ª casada; se o vocal está em silêncio ali (palavra que o cantor
    nem canta nesta versão, ex.: "Well"/"Oh"), dividem o tempo da 1ª palavra casada em vez de adiantar a linha.
    """
    n = len(tokens)
    if envelope is not None and timed:
        first = min(timed)
        if first > 0:
            fs, fe = timed[first]
            est = sum(len(t) * CHAR_MS + 60 for t in tokens[:first])
            v = envelope.first_voiced(fs - est, fs)
            if v is None or fs - v < MIN_WORD_MS:
                timed = {k: t for k, t in timed.items() if k != first}
                pairs = _distribute(tokens[: first + 1], fs, fe)
                for k, st in enumerate(pairs):
                    timed[k] = st
            elif v > fs - est:
                timed = dict(timed)
                for k, st in enumerate(_distribute(tokens[:first], v, fs)):
                    timed[k] = st
    starts: list[int | None] = [None] * n
    ends: list[int | None] = [None] * n
    for k, (s, e) in timed.items():
        starts[k], ends[k] = s, e
    k = 0
    while k < n:
        if starts[k] is not None:
            k += 1
            continue
        g0 = k
        while k < n and starts[k] is None:
            k += 1
        g1 = k  # [g0, g1) sem tempo
        texts = tokens[g0:g1]
        est = sum(len(t) * CHAR_MS + 60 for t in texts)
        left = ends[g0 - 1] if g0 > 0 else None
        right = starts[g1] if g1 < n else None
        if left is not None and right is not None:
            lo, hi = left, max(right, left + len(texts))
            if hi - lo > est * 2:  # buraco grande: palavras logo depois da anterior
                hi = lo + est
        elif left is not None:
            lo, hi = left, left + est
        else:
            assert right is not None
            lo, hi = max(0, right - est), right
        for idx, (s, e) in zip(range(g0, g1), _distribute(texts, lo, hi), strict=True):
            starts[idx], ends[idx] = s, e
    return [
        {"text": tokens[i], "start_ms": int(starts[i] or 0), "end_ms": int(ends[i] or 0)} for i in range(n)
    ]


def _interp(t_lrc: int, anchors: list[tuple[int, int]], offset: int) -> int:
    """Mapeia um tempo do LRC para o áudio interpolando entre âncoras (t_lrc, t_alinhado)."""
    if not anchors:
        return t_lrc + offset
    prev = next_ = None
    for a in anchors:
        if a[0] <= t_lrc:
            prev = a
        elif next_ is None:
            next_ = a
            break
    if prev and next_ and next_[0] > prev[0]:
        frac = (t_lrc - prev[0]) / (next_[0] - prev[0])
        return round(prev[1] + frac * (next_[1] - prev[1]))
    ref = prev or next_
    assert ref is not None
    return t_lrc + (ref[1] - ref[0])


def align_lines(
    lines: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    duration_ms: int | None = None,
    envelope: Envelope | dict[str, Any] | None = None,
    timed: bool = True,
) -> tuple[list[dict[str, Any]], AlignStats]:
    """Linhas da letra (com tempos LRC) + segmentos do Whisper (+ envelope do vocal, opcional) → linhas
    alinhadas (contrato) e estatísticas. `timed=False`: os tempos das linhas são só uma estimativa (letra em texto
    puro, ver `align_text`) — sem a 2ª passada com penalidade de tempo."""
    stats = AlignStats(lines=len(lines))
    env = envelope if isinstance(envelope, Envelope) or envelope is None else Envelope.from_dict(envelope)
    ww = whisper_words(segments, env)
    lw = lyric_words(lines)
    stats.words = len(lw)
    if not lines:
        return [], stats

    pairs = needleman_wunsch(lw, ww)
    offset0 = _median_offset(lw, ww, pairs) if timed else None
    if offset0 is not None:
        pairs = needleman_wunsch(lw, ww, offset0)
    stats.matched_words = len(pairs)

    # palavras casadas por linha
    per_line: dict[int, dict[int, tuple[int, int]]] = {}
    for i, j, _ in pairs:
        w = lw[i]
        per_line.setdefault(w.line, {})[w.pos] = (ww[j].start_ms, ww[j].end_ms)

    # linhas ancoradas (casamento suficiente, espalhamento plausível, monotônicas)
    lrc_start = [int(ln["start_ms"]) for ln in lines]
    timed_words: dict[int, list[dict[str, Any]]] = {}
    for li, ln in enumerate(lines):
        tokens = _line_tokens(ln)
        matched = per_line.get(li, {})
        n_norm = sum(1 for t in tokens if norm_word(t))
        need = 1 if n_norm <= 2 else min(2, n_norm) if n_norm <= 4 else max(2, round(n_norm * 0.4))
        if len(matched) < need:
            continue
        times = sorted(s for s, _ in matched.values())
        if times[-1] - times[0] > MAX_LINE_SPAN_MS:
            continue
        timed_words[li] = _anchored_words(tokens, matched, env)

    # monotonicidade: descarta âncoras que voltam no tempo (casamento espúrio)
    ordered = sorted(timed_words)
    keep: list[int] = []
    for li in ordered:
        s = timed_words[li][0]["start_ms"]
        while keep and timed_words[keep[-1]][0]["start_ms"] >= s:
            # a anterior ou esta está errada: fica a de casamento mais forte
            prev = keep[-1]
            if len(per_line.get(prev, {})) >= len(per_line.get(li, {})):
                s = None  # descarta esta
                break
            keep.pop()
        if s is not None:
            keep.append(li)
    anchored = {li: timed_words[li] for li in keep}

    anchors = [(lrc_start[li], anchored[li][0]["start_ms"]) for li in keep]
    if anchors:
        diffs = [a - b_lrc for b_lrc, a in anchors]
        stats.offset_ms = round(statistics.median(diffs))
        if len(anchors) >= 4:
            xs = [b for b, _ in anchors]
            span = max(xs) - min(xs)
            if span > 0:
                slope = _slope(xs, diffs)
                stats.drift_ms_per_min = round(slope * 60000, 1)
    offset = stats.offset_ms if stats.offset_ms is not None else (offset0 or 0)

    out: list[dict[str, Any]] = []
    for li, ln in enumerate(lines):
        tokens = _line_tokens(ln)
        if li in anchored:
            words = anchored[li]
            kind = "anchored"
            stats.anchored_lines += 1
        else:
            start = max(0, _interp(lrc_start[li], anchors, offset))
            dur = max(int(ln["end_ms"]) - int(ln["start_ms"]), 1)
            if anchors:
                stats.interpolated_lines += 1
                kind = "interpolated"
            else:
                stats.fallback_lines += 1
                kind = "fallback"
            words = [
                {"text": t, "start_ms": s, "end_ms": e}
                for t, (s, e) in zip(tokens, _distribute(tokens, start, start + dur), strict=True)
            ]
        stats.per_line.append(
            {
                "i": li,
                "kind": kind,
                "lrc_ms": lrc_start[li],
                "start_ms": words[0]["start_ms"] if words else None,
            }
        )
        if not words:
            continue
        item = {
            "start_ms": words[0]["start_ms"],
            "end_ms": words[-1]["end_ms"],
            "text": " ".join(w["text"] for w in words),
            "words": words,
        }
        if kind != "anchored":
            item["interpolated"] = True  # tempo estimado: o editor sinaliza (DESIGN §11.11)
        out.append(item)
    if duration_ms:
        out = [ln for ln in out if ln["start_ms"] < duration_ms]
        for ln in out:
            ln["end_ms"] = min(ln["end_ms"], duration_ms)
            for w in ln["words"]:
                w["end_ms"] = min(w["end_ms"], duration_ms)
                w["start_ms"] = min(w["start_ms"], w["end_ms"] - 1)
    return _finalize_sorted(out), stats


def pseudo_timed_lines(
    texts: list[str], segments: list[dict[str, Any]], duration_ms: int | None = None
) -> list[dict[str, Any]]:
    """Linhas sem tempo → tempos estimados espalhados (por nº de caracteres) entre a 1ª e a última palavra cantada
    (ou a música toda). Servem só de referência para interpolar linhas que não casarem com a voz."""
    starts = [
        float(w["start"]) for seg in segments for w in (seg.get("words") or []) if w.get("start") is not None
    ]
    ends = [float(w["end"]) for seg in segments for w in (seg.get("words") or []) if w.get("end") is not None]
    lo = round(min(starts) * 1000) if starts else 0
    hi = round(max(ends) * 1000) if ends else (duration_ms or 0)
    if hi <= lo:
        hi = lo + max(1, len(texts)) * 3000
    spans = _distribute(texts, lo, hi)
    return [{"start_ms": s, "end_ms": e, "text": t} for t, (s, e) in zip(texts, spans, strict=True)]


def align_text(
    texts: list[str],
    segments: list[dict[str, Any]],
    duration_ms: int | None = None,
    envelope: Envelope | dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], AlignStats]:
    """Letra oficial **sem tempos** (LRCLIB `plainLyrics` ou texto colado) + voz → linhas alinhadas (CR-001)."""
    lines = pseudo_timed_lines([t for t in texts if t.strip()], segments, duration_ms)
    return align_lines(lines, segments, duration_ms, envelope, timed=False)


def _finalize_sorted(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Garante ordem estrita antes do `_finalize` (que juntaria linhas com o mesmo início)."""
    prev = -1
    for ln in lines:
        if ln["start_ms"] <= prev:
            shift = prev + 1 - ln["start_ms"]
            ln["start_ms"] += shift
            ln["end_ms"] = max(ln["end_ms"], ln["start_ms"] + 1)
            ln["words"][0]["start_ms"] = ln["start_ms"]
        prev = ln["start_ms"]
    return builder._finalize(lines)


def _slope(xs: list[int], ys: list[int]) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else 0.0


# ---- detecção de offset -------------------------------------------------------------------------------------
def detect_offset(
    lines: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    envelope: Envelope | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Offset global sugerido para uma candidata (convenção do contrato: positivo = letra mais tarde).

    `suggested_offset_ms` = mediana de (início cantado − início da linha) nas linhas ancoradas;
    `confidence` ∈ [0, 1] combina cobertura (linhas ancoradas) e dispersão (MAD) das diferenças.
    """
    if not lines:
        return {"suggested_offset_ms": 0, "confidence": 0.0, "anchored_lines": 0, "lines": 0}
    _, stats = align_lines(lines, segments, envelope=envelope)
    diffs = [
        p["start_ms"] - p["lrc_ms"]
        for p in stats.per_line
        if p["kind"] == "anchored" and p["start_ms"] is not None
    ]
    if not diffs:
        return {"suggested_offset_ms": 0, "confidence": 0.0, "anchored_lines": 0, "lines": len(lines)}
    med = round(statistics.median(diffs))
    mad = statistics.median(abs(d - med) for d in diffs)
    spread = max(0.0, 1.0 - mad / 1500.0)  # MAD de 1,5 s → confiança 0
    conf = round(min(1.0, stats.coverage / 0.6) * spread, 2)
    return {
        "suggested_offset_ms": int(max(-600000, min(600000, med))),
        "confidence": conf,
        "anchored_lines": len(diffs),
        "lines": len(lines),
        "spread_ms": round(mad),
        "drift_ms_per_min": stats.drift_ms_per_min,
    }
