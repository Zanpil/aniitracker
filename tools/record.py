"""Record the game's sound, as the counter hears it, into data/enregistrements/<date>.wav.

Used to calibrate the sound detection: the .json next to the recording holds its start time on
the same clock as data/events.jsonl. Only the game is recorded (not Discord, a stream or music).
Usage: python tools/record.py [minutes, default 20]   (Ctrl+C or close the window to stop)
"""
import json, os, sys, time, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import game_audio

OUT = os.path.join(HERE, "..", "data", "enregistrements")


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    pid = game_audio.game_pid()
    if pid is None:
        sys.exit("Lance d'abord le jeu.")
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rate = game_audio.RATE
    with game_audio.GameAudio(pid) as game:
        w = wave.open(os.path.join(OUT, stamp + ".wav"), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        start = time.time()
        with open(os.path.join(OUT, stamp + ".json"), "w", encoding="utf-8") as f:
            json.dump({"start": start, "rate": rate, "device": f"jeu seul ({game_audio.GAME_EXE})"}, f)
        print(f"Enregistrement du son du jeu pendant {minutes:g} min max.")
        print("Joue normalement avec le compteur lancé. Ctrl+C pour arrêter.")
        written = 0
        try:
            while time.time() - start < minutes * 60:
                x = game.read()
                w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
                written += len(x)
                # if Windows ever delivers nothing for a while: pad so the timeline stays aligned
                gap = int((time.time() - start) * rate) - written - rate // 5
                if gap > 0:
                    w.writeframes(bytes(2 * gap))
                    written += gap
        except KeyboardInterrupt:
            pass
        finally:
            w.close()
    print(f"Terminé : {written / rate / 60:.1f} min dans {os.path.abspath(os.path.join(OUT, stamp))}.wav")


if __name__ == "__main__":
    main()
