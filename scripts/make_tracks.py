"""10 оригінальних фонових треків різних стилів для Shorts (усе синтезується кодом, права наші).
Використання: python scripts/make_tracks.py out_dir [секунд]"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_music  # noqa: E402

SR = 44100
CHORDS = {"m": [0, 3, 7], "M": [0, 4, 7], "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11], "7": [0, 4, 7, 10],
          "m9": [0, 3, 7, 10, 14], "6": [0, 4, 7, 9], "sus2": [0, 2, 7]}
MINOR_PENT = [0, 3, 5, 7, 10]
MAJOR_PENT = [0, 2, 4, 7, 9]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def place(buf, at, sig, g=1.0):
    i = int(at * SR)
    if i < 0 or i >= len(buf):
        return
    n = min(len(sig), len(buf) - i)
    buf[i:i + n] += sig[:n] * g


def osc(freq, n, kind="saw", duty=0.5):
    t = np.arange(n) / SR
    ph = (t * freq) % 1.0
    if kind == "sine":
        return np.sin(2 * np.pi * ph)
    if kind == "square":
        return np.where(ph < duty, 1.0, -1.0)
    if kind == "tri":
        return 4 * np.abs(ph - 0.5) - 1
    return 2 * ph - 1


def voice(freq, dur, kind="saw", a=0.005, d=4.0, detune=(0,), duty=0.5, rel=0.01, vib=0.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = freq * (1 + vib * np.sin(2 * np.pi * 5.5 * t)) if vib else freq
    sig = sum(osc(f * 2 ** (c / 1200), n, kind, duty) for c in detune) / len(detune)
    env = np.minimum(t / max(a, 1e-4), 1.0) * np.exp(-d * t)
    r = max(1, int(rel * SR))
    env[-r:] *= np.linspace(1, 0, min(r, n))[: len(env[-r:])]
    return sig * env


def bell(freq, dur, d=3.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-6 * t) + 0.25 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-12 * t)
    return sig * np.minimum(t / 0.003, 1) * np.exp(-d * t)


def rhodes(freq, dur, d=2.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-3 * t) + 0.12 * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-6 * t)
    sig *= 1 + 0.08 * np.sin(2 * np.pi * 4.5 * t)
    return sig * np.minimum(t / 0.004, 1) * np.exp(-d * t)


def kick(f0=55, sweep=120, length=0.25, decay=14, click=0.0):
    t = np.arange(int(length * SR)) / SR
    freq = f0 + sweep * np.exp(-t * 30)
    sig = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * decay)
    if click:
        sig[:int(0.003 * SR)] += click
    return sig


def snare(rng, length=0.2, tone=190, noise=0.8, decay=22):
    t = np.arange(int(length * SR)) / SR
    return (rng.uniform(-1, 1, len(t)) * noise + np.sin(2 * np.pi * tone * t) * 0.4) * np.exp(-t * decay)


def clap(rng):
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    noise = rng.uniform(-1, 1, n)
    bursts = sum(np.exp(-np.clip(t - o, 0, None) * 60) * (t >= o) for o in (0, 0.011, 0.022)) * 0.5
    return noise * (bursts + np.exp(-t * 18) * 0.5) * 0.8


def hat(rng, open_=False, bright=1.0):
    n = int((0.16 if open_ else 0.045) * SR)
    t = np.arange(n) / SR
    noise = hp(lp(rng.uniform(-1, 1, n), 10000), 6500)
    return noise * np.exp(-t * (24 if open_ else 85)) * bright * 1.6


def lp(x, fc):
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(np.fft.rfft(x) / np.sqrt(1 + (f / fc) ** 6), len(x))


def hp(x, fc):
    f = np.fft.rfftfreq(len(x), 1 / SR)
    r = (f / fc) ** 3
    return np.fft.irfft(np.fft.rfft(x) * r / np.sqrt(1 + r * r), len(x))


def reverb(x, rt=1.5, mix=0.25, seed=3):
    rng = np.random.default_rng(seed)
    n = int(rt * SR)
    t = np.arange(n) / SR
    ir = lp(rng.standard_normal(n), 4500) * np.exp(-6.9 * t / rt)
    ir /= np.sqrt(np.sum(ir ** 2))
    size = len(x) + n
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x * (1 - mix * 0.5) + wet * mix


def delay(x, secs, fb=0.4, taps=4, mix=0.5):
    out = x.copy()
    k = int(secs * SR)
    for i in range(1, taps + 1):
        out[i * k:] += x[: len(x) - i * k] * (fb ** i) * mix
    return out


def sidechain(n, kick_times, depth=0.7, tau=0.12):
    env = np.ones(n)
    for kt in kick_times:
        i = int(kt * SR)
        if i >= n:
            continue
        m = min(int(0.5 * SR), n - i)
        seg = 1 - depth * np.exp(-np.arange(m) / (tau * SR))
        env[i:i + m] = np.minimum(env[i:i + m], seg)
    return env


def chord_notes(root, kind, octave=0):
    return [root + i + 12 * octave for i in CHORDS[kind]]


def finish(mix, secs, peak=0.5):
    mix = mix[: int(secs * SR)]
    mix = lp(mix, 12000)
    mix = np.tanh(mix * 1.3) / np.tanh(1.3)
    mix *= 0.14 / max(np.sqrt(np.mean(mix ** 2)), 1e-6)  # однакова гучність у всіх треків
    mix = np.tanh(mix / peak) * peak
    fi, fo = int(0.15 * SR), int(1.8 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)
    mix[-fo:] *= np.linspace(1, 0, fo)
    return mix


def melody(rng, scale, root, bars, rhythm, lo=0, hi=1, step_max=2):
    """Випадкова (із зерном) мелодія по звукоряду: rhythm = список (крок16, довжина16) на 2 такти; повторюється."""
    notes, idx = [], len(scale) // 2
    motif = []
    for pos, ln in rhythm:
        idx = int(np.clip(idx + rng.integers(-step_max, step_max + 1), 0, len(scale) * 2 - 1))
        motif.append((pos, ln, root + scale[idx % len(scale)] + 12 * (idx // len(scale)) + 12 * lo))
    for b in range(0, bars, 2):
        for pos, ln, m in motif:
            notes.append((b * 16 + pos, ln, m))
    return notes


# ---------------------------------------------------------------- треки

def t_lofi(secs):
    bpm = 76
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(11)
    N = int((secs + 2) * SR)
    chords, bass, drums, mel = (np.zeros(N) for _ in range(4))
    prog = [(57, "m7"), (62, "m7"), (55, "7"), (60, "maj7")]
    bars = int(secs / (4 * beat)) + 2
    swing = lambda s: step * 0.33 if s % 2 else 0
    for b in range(bars):
        root, kind = prog[b % 4]
        notes = chord_notes(root, kind)
        for hit, ln in ((0, 2.6), (6, 1.6)):
            at = b * 4 * beat + hit * step
            for i, m in enumerate(notes):
                place(chords, at + i * 0.012, rhodes(hz(m), ln * beat), 0.22)
        place(bass, b * 4 * beat, voice(hz(root - 24), 1.8 * beat, "sine", d=1.2), 0.9)
        place(bass, b * 4 * beat + 2.5 * beat, voice(hz(root - 24 + (7 if b % 2 else 0)), 1.2 * beat, "sine", d=2), 0.7)
        for s in range(16):
            at = b * 4 * beat + s * step + swing(s)
            if s in (0, 10):
                place(drums, at, kick(48, 80, 0.3, 11), 0.9)
            if s in (4, 12):
                place(drums, at, lp(snare(rng, 0.25, 180, 0.5, 15), 3500), 0.7)
            if s % 2 == 0:
                place(drums, at, hat(rng, False, 0.6), 0.28 if s % 4 else 0.38)
    scale_notes = melody(rng, MINOR_PENT, 69, bars, [(0, 3), (4, 2), (7, 3), (12, 4)], 0, 1)
    for s, ln, m in scale_notes:
        if rng.random() < 0.75:
            place(mel, s * step + (step * 0.33 if int(s) % 2 else 0), rhodes(hz(m), ln * step * 1.4, 3), 0.2)
    crackle = (rng.random(N) > 0.9996) * rng.uniform(-1, 1, N) * 0.5
    mix = lp(chords + bass * 1.0, 4200) + drums * 0.9 + reverb(mel, 1.6, 0.4) + crackle * 0.5
    return finish(lp(mix, 6000), secs)


def t_synthwave(secs):
    bpm = 100
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(5)
    N = int((secs + 2) * SR)
    pad, bass, arp, drums, lead = (np.zeros(N) for _ in range(5))
    prog = [(41, "m7"), (37, "maj7"), (44, "maj7"), (39, "7")]
    bars = int(secs / (4 * beat)) + 2
    kicks = []
    for b in range(bars):
        root, kind = prog[b % 4]
        notes = chord_notes(root + 24, kind)
        for m in notes:
            place(pad, b * 4 * beat, voice(hz(m), 4 * beat, "saw", a=0.6, d=0.15, detune=(-9, 0, 9), rel=0.4), 0.16)
        for s in range(16):
            at = b * 4 * beat + s * step
            if s % 2 == 0:
                place(bass, at, voice(hz(root - 12 + (12 if s % 8 == 6 else 0)), step * 1.9, "saw", d=5), 0.6)
            note = notes[[0, 1, 2, 3 % len(notes), 2, 1][s % 6]] + 12
            place(arp, at, voice(hz(note), step * 1.5, "saw", d=9, detune=(-6, 6)), 0.2)
            if s in (0, 8):
                place(drums, at, kick(52, 100, 0.26, 12), 0.95)
                kicks.append(at)
            if s in (4, 12):
                place(drums, at, snare(rng, 0.3, 200, 0.9, 12), 0.6)
            if s % 2 == 0:
                place(drums, at, hat(rng, s % 8 == 6, 0.9), 0.2)
    for i, m in enumerate([72, 75, 77, 79, 77, 75, 72, 70]):
        for loop in range(0, bars, 2):
            place(lead, (loop * 4 + i * 1.0) * beat, voice(hz(m + 12 * (i % 2 == 0)), beat * 0.95, "saw", a=0.01, d=1.5, detune=(-8, 8), vib=0.004), 0.16)
    sc = sidechain(N, kicks, 0.4)
    bus = lp(pad, 2200) * sc + lp(bass, 800) * 1.0 * sc + delay(lp(arp, 4500), beat * 0.75, 0.45, 3) + reverb(lead, 1.8, 0.35)
    mix = bus + reverb(drums, 1.2, 0.3)
    return finish(mix, secs)


def t_house(secs):
    bpm = 126
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(21)
    N = int((secs + 2) * SR)
    stab, bass, drums, mel = (np.zeros(N) for _ in range(4))
    prog = [(50, "m7"), (46, "maj7"), (53, "maj7"), (48, "7")]
    bars = int(secs / (4 * beat)) + 2
    kicks = []
    for b in range(bars):
        root, kind = prog[b % 4]
        notes = chord_notes(root + 12, kind)
        for s in range(16):
            at = b * 4 * beat + s * step
            if s % 4 == 0:
                place(drums, at, kick(50, 130, 0.22, 16, 0.2), 1.0)
                kicks.append(at)
            if s in (4, 12):
                place(drums, at, clap(rng), 0.6)
            if s % 4 == 2:
                place(drums, at, hat(rng, True, 0.9), 0.25)
            elif s % 2 == 1:
                place(drums, at, hat(rng, False, 0.5), 0.14)
            if s in (3, 6, 10, 14):
                for m in notes:
                    place(stab, at, voice(hz(m), step * 1.6, "saw", d=9, detune=(-7, 7)), 0.14)
            if s % 4 == 2:
                place(bass, at, voice(hz(root - 12), step * 3, "saw", d=4), 0.55)
    for s, ln, m in melody(rng, MINOR_PENT, 62, bars, [(2, 2), (6, 2), (10, 3), (14, 2)], 1, 1):
        place(mel, s * step, voice(hz(m), ln * step, "square", d=6, duty=0.35), 0.15)
    sc = sidechain(N, kicks, 0.65, 0.14)
    mix = drums + (lp(bass, 700) + reverb(stab, 0.9, 0.25) * 1.0) * sc + delay(lp(mel, 5000), beat * 0.75, 0.4, 3)
    return finish(mix, secs)


def t_trap(secs):
    bpm = 140
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(31)
    N = int((secs + 2) * SR)
    sub, drums, mel, pad = (np.zeros(N) for _ in range(4))
    prog = [(36, "m"), (32, "M"), (39, "M"), (34, "M")]
    bars = int(secs / (4 * beat)) + 2
    for b in range(bars):
        root, kind = prog[b % 4]
        place(pad, b * 4 * beat, sum(voice(hz(m + 24), 4 * beat, "saw", a=0.3, d=0.2, detune=(-10, 10), rel=0.3) for m in chord_notes(root, kind)), 0.05)
        for s in range(16):
            at = b * 4 * beat + s * step
            if s in (0, 10, 13 if b % 2 else 11):
                place(drums, at, kick(45, 90, 0.3, 11), 0.8)
            if s == 8:
                place(drums, at, snare(rng, 0.3, 200, 0.7, 14), 0.8)
                place(drums, at, clap(rng), 0.5)
            roll = b % 4 == 3 and s >= 12
            if s % 2 == 0 or roll:
                place(drums, at + (step * 0.5 if roll and s % 2 else 0), hat(rng, False, 0.8), 0.2)
            if s % 2 == 0 and rng.random() < 0.25:
                place(drums, at + step, hat(rng, False, 0.8), 0.12)
        n = int(3.6 * beat * SR)
        t = np.arange(n) / SR
        f = hz(root) * (1 + 0.0 * t)
        s808 = np.sin(2 * np.pi * np.cumsum(f * (1 + 0.5 * np.exp(-t * 40))) / SR) * np.exp(-t * 1.1)
        place(sub, b * 4 * beat, np.tanh(s808 * 2.2) * 0.6, 1.0)
        if b % 2:
            place(sub, b * 4 * beat + 3 * beat, np.tanh(np.sin(2 * np.pi * hz(root + 7) * t[: int(beat * SR)]) * 2) * np.exp(-t[: int(beat * SR)] * 3), 0.5)
    for s, ln, m in melody(rng, MINOR_PENT, 63, bars, [(0, 2), (3, 2), (6, 2), (10, 3), (14, 2)], 1, 1):
        place(mel, s * step, bell(hz(m), ln * step * 2, 3.5), 0.25)
    mix = sub + drums + reverb(mel, 1.8, 0.35) + lp(pad, 1500)
    return finish(mix, secs)


def t_epic(secs):
    bpm = 88
    beat = 60 / bpm
    rng = np.random.default_rng(41)
    N = int((secs + 2) * SR)
    strings, low, perc, mel, rise = (np.zeros(N) for _ in range(5))
    prog = [(50, "m"), (46, "M"), (53, "M"), (48, "M")]
    bars = int(secs / (4 * beat)) + 2
    for b in range(bars):
        root, kind = prog[b % 4]
        at = b * 4 * beat
        for m in chord_notes(root + 12, kind) + [root + 24]:
            place(strings, at, voice(hz(m), 4 * beat, "saw", a=0.7, d=0.05, detune=(-14, -5, 5, 14), rel=0.5), 0.1)
        place(low, at, voice(hz(root - 12), 4 * beat, "saw", a=0.3, d=0.2, detune=(-6, 6), rel=0.4), 0.3)
        for hit, g in ((0, 1.0), (2.5, 0.6), (3, 0.7), (3.5, 0.8)):
            place(perc, at + hit * beat, kick(60, 70, 0.7, 5, 0) * 1.0, g * 0.9)
        place(perc, at, snare(rng, 0.6, 120, 0.5, 6), 0.4)
    for i, m in enumerate([74, 77, 81, 79, 77, 74, 72, 69]):
        for loop in range(0, bars, 2):
            place(mel, (loop * 4 + i) * beat, bell(hz(m + 12), beat * 3, 1.5), 0.2)
    ramp_n = int(2 * 4 * beat * SR)
    start = int((secs - 2 * 4 * beat) * SR) if secs > 8 * beat else 0
    noise = hp(rng.uniform(-1, 1, ramp_n), 1500) * np.linspace(0, 1, ramp_n) ** 2
    rise[start:start + ramp_n] = noise[: max(0, N - start)][:ramp_n] if start + ramp_n <= N else noise[: N - start]
    mix = reverb(lp(strings, 3000) + lp(low, 600) + perc * 0.9, 2.5, 0.45) + reverb(mel, 2.5, 0.5) + rise * 0.12
    return finish(mix, secs)


def t_funk(secs):
    bpm = 108
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(51)
    N = int((secs + 2) * SR)
    bass, stab, drums, horn = (np.zeros(N) for _ in range(4))
    prog = [(40, "m7"), (45, "7"), (40, "m7"), (47, "7")]
    bars = int(secs / (4 * beat)) + 2
    for b in range(bars):
        root, kind = prog[b % 4]
        notes = chord_notes(root + 24, kind)
        for s in range(16):
            at = b * 4 * beat + s * step
            if s in (0, 3, 6, 8, 10, 11, 14):
                m = root - 12 + (12 if s in (3, 11) else 0) + (7 if s == 14 else 0)
                place(bass, at, lp(voice(hz(m), step * 1.6, "saw", d=11), 1400), 0.7)
            if s in (2, 5, 7, 10, 13):
                for m in notes:
                    place(stab, at, voice(hz(m), step * 0.9, "square", d=14, duty=0.4), 0.09)
            if s in (0, 6, 10):
                place(drums, at, kick(56, 100, 0.2, 18), 0.9)
            if s in (4, 12):
                place(drums, at, snare(rng, 0.2, 200, 0.8, 24), 0.7)
            if s in (7, 9, 15):
                place(drums, at, snare(rng, 0.1, 200, 0.8, 40), 0.18)
            place(drums, at, hat(rng, s == 14, 0.9), 0.26 if s % 2 == 0 else 0.13)
        if b % 2:
            for i, m in enumerate([root + 31, root + 34, root + 31, root + 29]):
                place(horn, b * 4 * beat + (2 + i * 0.5) * beat, voice(hz(m), beat * 0.45, "saw", a=0.01, d=3, detune=(-5, 5)), 0.14)
    mix = lp(bass, 1500) + lp(stab, 5000) + drums + reverb(lp(horn, 3500), 0.8, 0.2)
    return finish(mix, secs)


def t_dnb(secs):
    bpm = 174
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(61)
    N = int((secs + 2) * SR)
    reese, drums, pad, mel = (np.zeros(N) for _ in range(4))
    prog = [(43, "m"), (39, "M"), (46, "M"), (41, "M")]
    bars = int(secs / (4 * beat)) + 2
    for b in range(bars):
        root, kind = prog[b % 4]
        at = b * 4 * beat
        for half in (0, 2):
            place(reese, at + half * beat, voice(hz(root - 12), 2 * beat, "saw", a=0.01, d=0.3, detune=(-18, -7, 7, 18)), 0.35)
        for m in chord_notes(root + 24, kind):
            place(pad, at, voice(hz(m), 4 * beat, "saw", a=0.5, d=0.1, detune=(-8, 8), rel=0.3), 0.07)
        for s in range(16):
            t_ = at + s * step
            if s in (0, 10):
                place(drums, t_, kick(52, 110, 0.18, 20), 0.95)
            if s in (4, 12):
                place(drums, t_, snare(rng, 0.2, 210, 0.9, 22), 0.8)
            if s in (7, 15, 9):
                place(drums, t_, snare(rng, 0.08, 210, 0.9, 50), 0.2)
            if s % 2 == 0:
                place(drums, t_, hat(rng, False, 0.9), 0.2)
    for s, ln, m in melody(rng, MINOR_PENT, 67, bars, [(0, 2), (3, 2), (6, 2), (10, 2), (13, 2)], 1, 1):
        place(mel, s * step, bell(hz(m), 0.5, 5), 0.18)
    mix = lp(reese, 900) + drums + reverb(lp(pad, 3000), 1.5, 0.4) + delay(reverb(mel, 1.0, 0.3), step * 3, 0.4, 3)
    return finish(mix, secs)


def t_ambient(secs):
    bpm = 68
    beat = 60 / bpm
    rng = np.random.default_rng(71)
    N = int((secs + 2) * SR)
    pad, bells, sub = (np.zeros(N) for _ in range(3))
    prog = [(48, "maj7"), (45, "m7"), (53, "maj7"), (55, "6")]
    span = 8 * beat
    count = int(secs / span) + 2
    for b in range(count):
        root, kind = prog[b % 4]
        at = b * span
        for m in chord_notes(root + 12, kind):
            place(pad, at, voice(hz(m), span * 1.15, "saw", a=1.6, d=0.0, detune=(-12, -4, 4, 12), rel=1.5), 0.07)
            place(pad, at, voice(hz(m + 12), span * 1.15, "sine", a=2.0, d=0.0, rel=1.5), 0.05)
        place(sub, at, voice(hz(root - 12), span * 1.1, "sine", a=1.0, d=0.0, rel=1.0), 0.4)
    scale = [0, 2, 4, 7, 9]
    for i in range(int(secs / (beat / 2)) + 4):
        if rng.random() < 0.45:
            deg = scale[rng.integers(0, 5)] + 12 * rng.integers(0, 2)
            place(bells, i * beat / 2, bell(hz(72 + deg), 2.5, 1.3), 0.14)
    mix = reverb(lp(pad, 2400), 3.5, 0.55) + sub + delay(reverb(bells, 3.0, 0.55), beat * 0.75, 0.5, 4)
    return finish(mix, secs)


def t_pop(secs):
    bpm = 112
    beat = 60 / bpm
    step = beat / 4
    rng = np.random.default_rng(81)
    N = int((secs + 2) * SR)
    uke, bass, drums, lead = (np.zeros(N) for _ in range(4))
    prog = [(60, "M"), (55, "M"), (57, "m"), (53, "M")]
    bars = int(secs / (4 * beat)) + 2
    for b in range(bars):
        root, kind = prog[b % 4]
        notes = chord_notes(root, kind)
        for s in (0, 3, 6, 8, 11, 14):
            at = b * 4 * beat + s * step
            for i, m in enumerate(sorted(notes, reverse=True) + [notes[0] + 12]):
                place(uke, at + i * 0.012, voice(hz(m + 12), 0.35, "tri", a=0.002, d=9), 0.2)
        for s in range(16):
            at = b * 4 * beat + s * step
            if s in (0, 8, 11):
                place(drums, at, kick(58, 90, 0.2, 16), 0.85)
            if s in (4, 12):
                place(drums, at, clap(rng), 0.7)
            if s % 2 == 0:
                place(drums, at, hat(rng, False, 0.7), 0.2)
            if s % 4 == 0:
                place(bass, at, voice(hz(root - 12 + (7 if s == 8 else 0)), step * 3.5, "tri", d=3), 0.7)
    for s, ln, m in melody(np.random.default_rng(5), MAJOR_PENT, 72, bars, [(0, 2), (2, 2), (4, 4), (8, 2), (10, 2), (12, 3)], 0, 1, 1):
        place(lead, s * step, voice(hz(m), ln * step * 0.95, "sine", a=0.01, d=1.2, vib=0.006), 0.3)
    mix = uke + bass + drums + reverb(lead, 1.0, 0.25)
    return finish(mix, secs)


TRACKS = [
    ("01_pixel-run_chiptune", "Чіптюн, бадьорий ігровий, 124 BPM", make_music.generate),
    ("02_late-night_lofi", "Lo-fi, спокійний, 76 BPM", t_lofi),
    ("03_neon-drive_synthwave", "Synthwave, ретро-неон, 100 BPM", t_synthwave),
    ("04_afterparty_house", "Хаус, танцювальний, 126 BPM", t_house),
    ("05_dark-bells_trap", "Трап, темний, з 808, 140 BPM", t_trap),
    ("06_final-boss_epic", "Кінематографічний, епічний, 88 BPM", t_epic),
    ("07_groove-street_funk", "Фанк, грувовий, 108 BPM", t_funk),
    ("08_rush-hour_dnb", "Драм-енд-бейс, швидкий, 174 BPM", t_dnb),
    ("09_cloud-garden_ambient", "Ембієнт, мрійливий, без бітів", t_ambient),
    ("10_sunny-side_pop", "Весела поп-доріжка, 112 BPM", t_pop),
]

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    for name, label, fn in TRACKS:
        make_music.write_wav(str(out / f"{name}.wav"), fn(secs))
        print(name, "ok")
