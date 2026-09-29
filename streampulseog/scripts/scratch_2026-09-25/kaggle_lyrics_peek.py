import json, subprocess, re, tempfile, os

k = json.load(open("/Users/uttkarshtyagi/Downloads/kaggle.json"))
auth = k["username"] + ":" + k["key"]


def api(path):
    r = subprocess.run(["curl", "-sS", "-L", "-m", "60", "-u", auth, "https://www.kaggle.com/api/v1/" + path], capture_output=True, text=True)
    return r.stdout


for ref in ["serkantysz/550k-spotify-songs-audio-lyrics-and-genres", "devdope/900k-spotify"]:
    print("=====", ref)
    try:
        d = json.loads(api("datasets/view/" + ref))
        print(d.get("title"), "| license:", d.get("licenseName"), "| MB", round((d.get("totalBytes") or 0) / 1e6), "| updated", (d.get("lastUpdated") or "")[:10])
        print((d.get("description") or "")[:900].replace("\n", " "))
    except Exception as e:
        print("view fail", e)
    # try file list
    out = api("datasets/list/" + ref + "/files")
    print("files api:", out[:300].replace("\n", " "))
