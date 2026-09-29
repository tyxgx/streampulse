import json, subprocess, urllib.parse

k = json.load(open("/Users/uttkarshtyagi/Downloads/kaggle.json"))
auth = k["username"] + ":" + k["key"]


def curl(url, kaggle=False):
    cmd = ["curl", "-sS", "-L", "-m", "60"] + (["-u", auth] if kaggle else []) + [url]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


print("=== HuggingFace dataset cards ===")
for ds in ["ozefe/spotify_audio_features", "GildasLeDrogoff/spotify-huge-track-analysis-dataset",
           "maharshipandya/spotify-tracks-dataset", "vishnupriyavr/spotify-million-song-dataset"]:
    try:
        d = json.loads(curl("https://huggingface.co/api/datasets/" + ds))
        tags = [t for t in d.get("tags", []) if t.startswith("license") or t.startswith("size_categories")]
        files = [(s["rfilename"]) for s in d.get("siblings", [])][:6]
        print(ds, "| downloads", d.get("downloads"), "| likes", d.get("likes"), "| updated", d.get("lastModified"),
              "| tags", tags, "| files", files, "| n files", len(d.get("siblings", [])))
    except Exception as e:
        print(ds, "fail", e)

print("\n=== Kaggle search: audio features / lyrics / listeners ===")
for q in ["spotify audio features tracks", "spotify lyrics", "spotify million playlist", "spotify artists genres followers"]:
    out = curl("https://www.kaggle.com/api/v1/datasets/list?search=" + urllib.parse.quote(q) + "&sortBy=votes&pageSize=6", kaggle=True)
    try:
        for d in json.loads(out)[:6]:
            print(q, "->", d.get("ref"), "|", d.get("title"), "| MB", round((d.get("totalBytes") or 0) / 1e6), "| updated", (d.get("lastUpdated") or "")[:10], "| votes", d.get("voteCount"))
    except Exception as e:
        print(q, "fail", out[:150])
