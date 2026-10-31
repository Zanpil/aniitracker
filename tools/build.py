"""Builds the counter for Windows, on your PC or on GitHub (.github/workflows/version.yml).

  python tools/build.py          distribution/Compteur pity Aniimo.exe, one file, with PyInstaller;
                                 its properties carry the name and the version (VERSION in overlay.py)
  python tools/build.py zip      Compteur-pity-Aniimo.zip: the exe and LISEZMOI.txt, for a release
  python tools/build.py notes notes.md   this version's part of CHANGELOG.md, for the release page
  python tools/build.py tag v1.3.0   fails unless the tag is this version (a release must not
                                 carry an exe that thinks it is another version: it would keep
                                 offering itself as an update)

Needs: pip install -r requirements.txt pyinstaller
"""
import os, re, subprocess, sys, tempfile, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Compteur pity Aniimo"
DIST = os.path.join(ROOT, "distribution")
ZIP = os.path.join(ROOT, "Compteur-pity-Aniimo.zip")
# what goes inside the exe: the sounds' fingerprints (not the sounds), the image models, the icon
BUNDLED = ["sons/empreintes.npz", "images/niv.png", "images/culbuton.png", "images/floraison_titre.png",
           "images/icone.ico"]
PAGE = "https://zanpil.github.io/compteur-pity-aniimo/"

VERSION_FILE = """# -*- coding: utf-8 -*-
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'Zanpil'),
      StringStruct('FileDescription', '{name}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', '© 2026 Zanpil, licence MIT'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', '{name}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)
"""


def version():
    with open(os.path.join(ROOT, "overlay.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', f.read(), re.M).group(1)


def exe():
    work = os.path.join(tempfile.gettempdir(), "compteur-pity-build")
    os.makedirs(work, exist_ok=True)
    v = version()
    info = os.path.join(work, "version.txt")
    with open(info, "w", encoding="utf-8") as f:
        f.write(VERSION_FILE.format(numbers=tuple(int(x) for x in v.split(".")) + (0,), version=v, name=NAME))
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", NAME, "--icon", os.path.join(ROOT, "images", "icone.ico"), "--version-file", info,
           "--distpath", DIST, "--workpath", work, "--specpath", work]
    for path in BUNDLED:
        cmd += ["--add-data", os.path.join(ROOT, path) + os.pathsep + os.path.dirname(path)]
    subprocess.run(cmd + [os.path.join(ROOT, "overlay.py")], check=True, cwd=ROOT)
    print(f"Fait : distribution\\{NAME}.exe, version {v}")


def make_zip():
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(DIST, NAME + ".exe"), NAME + ".exe")
        z.write(os.path.join(DIST, "LISEZMOI.txt"), "LISEZMOI.txt")
    size = f"{os.path.getsize(ZIP):,}".replace(",", " ")
    print(f"Fait : {os.path.basename(ZIP)}, {size} octets")


def notes(path):
    v = version()
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        part = re.search(rf"^## {re.escape(v)}\b[^\n]*\n(.*?)(?=^## |\Z)", f.read(), re.M | re.S)
    text = (part.group(1).strip() + "\n\n") if part else ""
    text += ("**Installer** : téléchargez `Compteur-pity-Aniimo.zip` ci-dessous et dézippez-le dans un dossier "
             "à vous. **Déjà installé** (1.3.0 ou plus) : menu du compteur > *Installer la version…*, vos compteurs "
             f"et réglages sont gardés.\n\n[Code signing policy]({PAGE}#signature) · "
             "[Nouveautés](https://github.com/Zanpil/compteur-pity-aniimo/blob/main/CHANGELOG.md)\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Fait : {path}")


def tag(name):
    if name.lstrip("v") != version():
        sys.exit(f"Le tag {name} ne correspond pas à la version du code ({version()}) : changez VERSION dans overlay.py.")
    print(f"{name} : version {version()}, d'accord")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "exe"
    if what == "exe":
        exe()
    elif what == "zip":
        make_zip()
    elif what == "notes" and len(sys.argv) > 2:
        notes(sys.argv[2])
    elif what == "tag" and len(sys.argv) > 2:
        tag(sys.argv[2])
    else:
        sys.exit(__doc__)
