"""Extract the catch / Lucky reference sounds from Aniimo's Wwise banks into sons/*.wav.

Sounds are looked up by Wwise event name in SoundbanksInfo.xml, so media ids may change with
game updates. The embedded .wem (Wwise Vorbis) are decoded with vgmstream-cli (tools/vgmstream).
Every other one-shot catch sound goes to sons/candidats/, to find which ones the game plays.
Usage: python tools/extract_sounds.py [<Aniimo install dir>]   (default: found in the Steam libraries)
"""
import glob, os, re, struct, subprocess, sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "sons")
VGMSTREAM = os.path.join(HERE, "vgmstream", "vgmstream-cli.exe")


def find_game():
    """Aniimo's folder in one of the Steam libraries (Steam's registry key and libraryfolders.vdf)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = winreg.QueryValueEx(key, "SteamPath")[0]
    except OSError:
        return None
    libraries = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        with open(vdf, encoding="utf-8", errors="replace") as f:
            libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    for library in libraries:
        path = os.path.join(library, "steamapps", "common", "Aniimo")
        if os.path.isdir(path):
            return path
    return None


GAME = sys.argv[1] if len(sys.argv) > 1 else find_game() or ""
SOUNDS = {
    "lucky": "SFX_Catch_Lucky",
    "super": "SFX_Catch_SuperLucky",
    "struggle_lucky": "se_catchball_normalball_magiccube_struggle_lucky",
    "success": "se_catchcommon_success",
    "failure": "se_catchcommon_failure",
    "ball_success": "se_catchball_normalball_magiccube_struggle_successful",
    "ball_success_end": "se_catchball_normalball_magiccube_struggle_successful_end",
    "ball_failure": "se_catchball_normalball_failure",
}


CANDIDATES = re.compile(r"\\Catch\\(catchball|pokeball)\\")


def find_media(info_xml):
    """{event name: (bank file, media id)} for the wanted events and every one-shot catch sound."""
    wanted, found = set(SOUNDS.values()), {}
    for bank in ET.parse(info_xml).getroot().iter("SoundBank"):
        for ev in bank.iter("Event"):
            name = ev.get("Name")
            candidate = (CANDIDATES.search(ev.get("ObjectPath", "")) and ev.get("DurationType") == "OneShot"
                         and "OtherPlayer" not in name)
            if (name in wanted or candidate) and name not in found:
                refs = [int(m.get("Id")) for m in ev.iter("MediaRef")]
                if refs:
                    found[name] = (bank.findtext("Path"), refs[0])
    return found


def extract(banks_dir, bank, mid, wav):
    """Decode one embedded media to wav; returns an error text or None."""
    wem = wav[:-4] + ".wem"
    try:
        data = read_wem(os.path.join(banks_dir, bank), mid)
    except KeyError as e:  # streamed media, not inside the bank
        return str(e)
    with open(wem, "wb") as f:
        f.write(data)
    r = subprocess.run([VGMSTREAM, "-o", wav, wem], capture_output=True, text=True)
    os.remove(wem)
    return None if r.returncode == 0 else r.stderr.strip()[:200]


def read_wem(bank_path, media_id):
    """Slice one embedded .wem out of a bank (DIDX index + DATA chunk)."""
    data = open(bank_path, "rb").read()
    chunks, pos = {}, 0
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        chunks[tag] = (pos + 8, size)
        pos += 8 + size
    didx, didx_size = chunks[b"DIDX"]
    base = chunks[b"DATA"][0]
    for i in range(didx, didx + didx_size, 12):
        mid, off, size = struct.unpack_from("<III", data, i)
        if mid == media_id:
            return data[base + off: base + off + size]
    raise KeyError(f"media {media_id} absent de {os.path.basename(bank_path)}")


def main():
    hits = glob.glob(os.path.join(GAME, "*_Data", "StreamingAssets", "Audio", "GeneratedSoundBanks", "Windows",
                                  "SoundbanksInfo.xml"))
    if not hits:
        sys.exit(f"Aniimo introuvable{' dans ' + GAME if GAME else ''}. Donne le dossier du jeu en argument.")
    banks_dir = os.path.dirname(hits[0])
    media = find_media(hits[0])
    os.makedirs(os.path.join(OUT, "candidats"), exist_ok=True)
    for out_name, event in SOUNDS.items():
        if event not in media:
            print(f"  {out_name:17} événement {event} introuvable")
            continue
        err = extract(banks_dir, *media[event], os.path.join(OUT, f"{out_name}.wav"))
        print(f"  {out_name:17} {'ÉCHEC ' + err if err else 'ok'}")
    others = sorted(set(media) - set(SOUNDS.values()))
    failed = [name for name in others
              if extract(banks_dir, *media[name], os.path.join(OUT, "candidats", f"{name}.wav"))]
    print(f"  + {len(others) - len(failed)} autres sons de capture dans candidats/"
          + (f" ({len(failed)} non extraits : {', '.join(failed)})" if failed else ""))
    print(f"Sons écrits dans {os.path.abspath(OUT)}")


if __name__ == "__main__":
    main()
