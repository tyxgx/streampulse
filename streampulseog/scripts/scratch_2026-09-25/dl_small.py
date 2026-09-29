import json, subprocess, os, re, sys, time

k = json.load(open("/Users/uttkarshtyagi/Downloads/kaggle.json"))
auth = k["username"] + ":" + k["key"]
base = "https://www.kaggle.com/api/v1/datasets/download/gonzalopezgil/spotify-charts-daily-updated?fileName="
out = "/Users/uttkarshtyagi/job/projects/streampulseOG/data/raw/kaggle_gonzalopezgil"
os.makedirs(out, exist_ok=True)

for fn in ["artists.csv", "albums.csv", "links.csv", "artwork.csv", "songs.csv", "artist_listeners_daily.csv"]:
    dest = os.path.join(out, fn)
    t = time.time()
    r = subprocess.run(["curl", "-sS", "-L", "-m", "900", "-u", auth, "-o", dest, "-w", "%{http_code} %{size_download}", base + fn],
                       capture_output=True, text=True)
    print(fn, r.stdout, r.stderr[:200], "%.0fs" % (time.time() - t), flush=True)

# probe the names of the big gz files (HEAD-like: first bytes only)
for fn in ["charts_songs_daily.csv.gz", "charts_songs_daily.csv", "charts_artists_daily.csv.gz", "charts_artists_daily.csv",
           "charts_albums_weekly.csv.gz", "charts_albums_weekly.csv"]:
    r = subprocess.run(["curl", "-sS", "-L", "-m", "60", "-u", auth, "-r", "0-200", "-o", "/dev/null", "-w", "%{http_code}", base + fn],
                       capture_output=True, text=True)
    print("probe", fn, r.stdout, flush=True)
