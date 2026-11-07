"""Оригінальна фонова музика для Shorts: бадьорий чіптюн (am-F-C-G, 124 BPM), згенерований кодом.
Це наша власна композиція, тож без чужих авторських прав і Content ID.
Використання: python scripts/make_music.py out.wav [секунд]"""

from __future__ import annotations

import sys
import wave

import numpy as np

SR = 44100
BPM = 124
BEAT = 60 / BPM
# акорди по тактах (кореневі ноти MIDI та три тони акорду)
PROGRESSION = [
    (45, (57, 60, 64)),  # Am
    (41, (57, 60, 65)),  # F
    (48, (60, 64, 67)),  # C
    (43, (59, 62, 67)),  # G
]
LEAD = [  # мелодія на 8 тактів: (доля, довжина в долях, MIDI)
    (0, 1.5, 76), (1.5, 0.5, 72), (2, 1, 69), (3, 1, 72),
    (4, 1.5, 77), (5.5, 0.5, 74), (6, 1, 72), (7, 1, 69),
    (8, 1.5, 79), (9.5, 0.5, 76), (10, 1, 72), (11, 1, 76),
    (12, 1.5, 74), (13.5, 0.5, 71), (14, 2, 67),
    (16, 1.5, 76), (17.5, 0.5, 72), (18, 1, 69), (19, 1, 72),
    (20, 1.5, 77), (21.5, 0.5, 81), (22, 1, 77), (23, 1, 74),
    (24, 1.5, 79), (25.5, 0.5, 76), (26, 1, 72), (27, 1, 67),
    (28, 2, 69), (30, 2, 76),
]


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def place(buf: np.ndarray, start: float, sig: np.ndarray, gain: float = 1.0) -> None:
    i = int(start * SR)
    if i >= len(buf):
        return
    n = min(len(sig), len(buf) - i)
    buf[i : i + n] += sig[:n] * gain


def tone(freq: float, dur: float, kind: str = "square", duty: float = 0.5, decay: float = 6.0) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    phase = (t * freq) % 1.0
    if kind == "square":
        wave_ = np.where(phase < duty, 1.0, -1.0)
    elif kind == "tri":
        wave_ = 4 * np.abs(phase - 0.5) - 1
    else:
        wave_ = 2 * phase - 1
    attack = np.minimum(t / 0.004, 1.0)
    return wave_ * attack * np.exp(-decay * t / max(dur, 1e-3))


def kick(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.22 * SR)) / SR
    freq = 50 + 110 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 14)


def snare(rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(0.18 * SR)) / SR
    return (rng.uniform(-1, 1, len(t)) * 0.8 + np.sin(2 * np.pi * 190 * t) * 0.4) * np.exp(-t * 22)


def hat(rng: np.random.Generator, open_: bool = False) -> np.ndarray:
    t = np.arange(int((0.12 if open_ else 0.04) * SR)) / SR
    noise = rng.uniform(-1, 1, len(t))
    f = np.fft.rfftfreq(len(noise), 1 / SR)  # лише верхи, але без різкого шипіння вище 10 кГц
    spec = np.fft.rfft(noise) * np.minimum(f / 6000, 1.0) / np.sqrt(1 + (f / 10000) ** 6)
    return np.fft.irfft(spec, len(noise)) * np.exp(-t * (30 if open_ else 90)) * 1.6


def generate(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    bars = 8
    loop_len = bars * 4 * BEAT
    total = max(seconds, loop_len) + 1
    buf = np.zeros(int(total * SR))
    loops = int(np.ceil(seconds / loop_len)) + 1
    for loop in range(loops):
        base = loop * loop_len
        for bar in range(bars):
            root, chord = PROGRESSION[bar % 4]
            for step in range(16):  # шістнадцяті
                at = base + bar * 4 * BEAT + step * BEAT / 4
                if step % 4 == 0:
                    place(buf, at, kick(rng), 0.9)
                if step in (4, 12):
                    place(buf, at, snare(rng), 0.55)
                if step % 2 == 0:
                    place(buf, at, hat(rng, open_=step % 8 == 6), 0.22 if step % 4 else 0.3)
                if step % 2 == 0:  # бас вісімками
                    place(buf, at, tone(hz(root + (12 if step % 8 == 6 else 0)), BEAT / 2, "tri", decay=3), 0.5)
                note = chord[[0, 1, 2, 1][step % 4]] + 12  # арпеджіо
                place(buf, at, tone(hz(note), BEAT / 4, "square", 0.25, decay=5), 0.16)
        for beat, length, midi in LEAD:
            if loop == 0 and beat < 0:
                continue
            place(buf, base + beat * BEAT, tone(hz(midi), length * BEAT * 0.9, "square", 0.5, decay=2.5), 0.2)
    buf = buf[: int(seconds * SR)]
    buf = np.tanh(buf * 1.4) / np.tanh(1.4)  # м'який лімітер
    buf *= 0.14 / max(np.sqrt(np.mean(buf ** 2)), 1e-6)  # гучність як у решти треків
    buf = np.tanh(buf / 0.5) * 0.5
    fade_in, fade_out = int(0.2 * SR), int(1.8 * SR)
    buf[:fade_in] *= np.linspace(0, 1, fade_in)
    buf[-fade_out:] *= np.linspace(1, 0, fade_out)
    return buf


def write_wav(path: str, mono: np.ndarray) -> None:
    pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(stereo.tobytes())


if __name__ == "__main__":
    write_wav(sys.argv[1], generate(float(sys.argv[2]) if len(sys.argv) > 2 else 16.0))
