import json, subprocess, zlib, re, tempfile, os

k = json.load(open("/Users/uttkarshtyagi/Downloads/kaggle.json"))
auth = k["username"] + ":" + k["key"]
base = "https://www.kaggle.com/api/v1/datasets/download/gonzalopezgil/spotify-charts-daily-updated?fileName="
files = ["artists.csv", "songs.csv", "albums.csv", "links.csv", "artwork.csv",
         "artist_listeners_daily.csv", "charts_artists_daily.csv.gz", "charts_albums_weekly.csv.gz",
         "charts_songs_daily.csv.gz"]

for fn in files:
    body = tempfile.mktemp()
    hdr = tempfile.mktemp()
    subprocess.run(["curl", "-sS", "-L", "-m", "90", "-u", auth, "-r", "0-60000", "-D", hdr, "-o", body, base + fn],
                   capture_output=True, text=True)
    h = open(hdr, errors="ignore").read()
    total = re.findall(r"content-range:\s*bytes \d+-\d+/(\d+)", h, re.I)
    status = re.findall(r"^HTTP/[\d.]+ (\d+)", h, re.M)
    raw = open(body, "rb").read()
    text = ""
    try:
        if fn.endswith(".gz"):
            text = zlib.decompressobj(31).decompress(raw).decode("utf-8", "ignore")
        else:
            text = raw.decode("utf-8", "ignore")
    except Exception as e:
        text = "decode fail: %s | %r" % (e, raw[:120])
    lines = text.splitlines()
    print("=== %s | http %s | total bytes %s" % (fn, status[-1] if status else "?", total[-1] if total else "n/a"))
    for ln in lines[:3]:
        print("  ", ln[:400])
    os.remove(body); os.remove(hdr)
