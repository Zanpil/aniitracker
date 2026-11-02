"""New versions, from the GitHub releases of the project.

The counter asks GitHub for the number of the latest version (only if the player said yes, or
from the menu), downloads its zip when the player installs it, checks it against the SHA-256 that
GitHub publishes for it, and swaps the new exe in for the running one. Windows lets a running
program be renamed but not overwritten: the old exe becomes "<exe>.old", deleted by the new one.
It is the counter's only access to the network. Counters and settings (data\\, config.json) are
never in the zip, so they stay as they are.
"""
import hashlib, json, os, shutil, subprocess, sys, time, urllib.request, zipfile

REPO = "Zanpil/aniitracker"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "Aniitracker.zip"
EXE = "Aniitracker.exe"
# its name up to 1.3.0: a release also carries its zip under that name, the only one a 1.3.0 counter
# looks for; what is left under that name once the counter runs as Aniitracker.exe is cleaned up
OLD_ASSET, OLD_EXE = "Compteur-pity-Aniimo.zip", "Compteur pity Aniimo.exe"
REPLACED = ("LISEZMOI.txt",)            # updated with the exe
OBSOLETE = ("Tester le compteur.bat",)  # shipped by older versions, replaced by the menu


def parse(version):
    """"v1.10.0" -> (1, 10, 0); None if it is not a version number."""
    try:
        return tuple(int(x) for x in version.strip().lstrip("vV").split("."))
    except ValueError:
        return None


def request(url, current, accept):
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Aniitracker/{current}"})


def latest(current, timeout=10):
    """The latest release when it is newer than `current`, else None."""
    with urllib.request.urlopen(request(API, current, "application/vnd.github+json"), timeout=timeout) as r:
        release = json.load(r)
    version = release["tag_name"].lstrip("vV")
    if not parse(version) or not parse(current) or parse(version) <= parse(current):
        return None
    asset = next((a for a in release.get("assets", []) if a["name"] == ASSET), {})
    return {"version": version, "notes": release.get("body") or "", "page": release.get("html_url") or PAGE,
            "url": asset.get("browser_download_url"), "size": asset.get("size") or 0,
            "digest": asset.get("digest")}


def download(info, folder, current, progress=lambda done, total: None):
    """The release's zip, saved in `folder` and checked against GitHub's SHA-256; returns its path."""
    path = os.path.join(folder, ASSET + ".part")
    sha, done = hashlib.sha256(), 0
    with urllib.request.urlopen(request(info["url"], current, "application/octet-stream"), timeout=30) as r, \
            open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or info["size"] or 0)
        while chunk := r.read(1 << 16):
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            progress(done, total)
    if info["digest"] and info["digest"].lower() != "sha256:" + sha.hexdigest():
        os.remove(path)
        raise OSError("le fichier téléchargé est abîmé (son empreinte ne correspond pas)")
    return path


def signer(path):
    """Who signed the program (Authenticode), or None if it is not signed or the signature is wrong."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:SIGNED_FILE; "
              "if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         env={**os.environ, "SIGNED_FILE": path}, capture_output=True, text=True,
                         timeout=60, creationflags=0x08000000)  # CREATE_NO_WINDOW
    return out.stdout.strip() or None


def install(zip_path, exe):
    """Put the zip's exe in place of the running one (renamed <exe>.old) and its readme beside it."""
    folder = os.path.dirname(exe)
    new = exe[:-4] + ".new.exe"
    with zipfile.ZipFile(zip_path) as z:
        members = {os.path.basename(n): n for n in z.namelist() if not n.endswith("/")}
        if EXE not in members:
            raise OSError("le zip ne contient pas le programme")
        with z.open(members[EXE]) as src, open(new, "wb") as dst:
            shutil.copyfileobj(src, dst)  # zipfile checks the file's CRC once it is read through
        extras = [(name, z.read(members[name])) for name in REPLACED if name in members]
    # a signed counter only takes a new version signed by the same publisher
    mine = signer(exe)
    if mine and signer(new) != mine:
        os.remove(new)
        raise OSError("la nouvelle version n'est pas signée par le même éditeur")
    old = exe + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:  # still held by a counter that has not finished quitting
        old = f"{exe}.{int(time.time())}.old"
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)  # keep the running version
        raise
    for name, data in extras:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
    for name in OBSOLETE:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    os.remove(zip_path)


def cleanup(exe):
    """What an update leaves behind: the old exe once it has quit, an interrupted download; and what an
    older version left beside this one, by hand: the program under its old name, the test .bat."""
    folder, name = os.path.dirname(exe), os.path.basename(exe)
    names = os.listdir(folder)
    for n in (ASSET + ".part", OLD_ASSET + ".part", name[:-4] + ".new.exe") + OBSOLETE:
        if n in names:
            try:
                os.remove(os.path.join(folder, n))
            except OSError:
                pass
    old = [os.path.join(folder, n) for n in names if n.startswith((name, OLD_EXE)) and n.endswith(".old")]
    if OLD_EXE in names and name.lower() != OLD_EXE.lower():
        old.append(os.path.join(folder, OLD_EXE))
    for _ in range(60):  # the old counter can take a few seconds to quit
        for path in list(old):
            try:
                os.remove(path)
                old.remove(path)
            except FileNotFoundError:
                old.remove(path)
            except OSError:
                pass
        if not old:
            return
        time.sleep(0.5)


def start(command, folder):
    """Start a program of its own (the counter again, or its self-test).

    A one-file exe started from another one would reuse the files the first one unpacked, which
    are deleted when it quits: PyInstaller's variables are taken out of its environment."""
    unpacked = getattr(sys, "_MEIPASS", None)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("_PYI_") and not (unpacked and v.startswith(unpacked))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(command, cwd=folder, env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
