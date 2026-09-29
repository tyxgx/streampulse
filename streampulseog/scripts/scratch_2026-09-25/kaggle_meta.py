import json, subprocess

k = json.load(open("/Users/uttkarshtyagi/Downloads/kaggle.json"))
auth = k["username"] + ":" + k["key"]


def get(url):
    r = subprocess.run(["curl", "-sS", "-m", "60", "-u", auth, url], capture_output=True, text=True)
    return r.stdout, r.stderr


out, err = get("https://www.kaggle.com/api/v1/datasets/view/gonzalopezgil/spotify-charts-daily-updated")
try:
    d = json.loads(out)
    keep = ["title", "subtitle", "lastUpdated", "currentVersionNumber", "totalBytes",
            "downloadCount", "viewCount", "usabilityRating", "expectedUpdateFrequency"]
    print({x: d.get(x) for x in keep})
    print("files:", [(f.get("name"), f.get("totalBytes")) for f in d.get("files", [])] or "n/a")
    print("keys:", list(d.keys())[:40])
except Exception as e:
    print("parse fail", e, out[:300], err[:200])

out, err = get("https://www.kaggle.com/api/v1/datasets/list/gonzalopezgil/spotify-charts-daily-updated/files")
print(out[:1500], err[:200])
