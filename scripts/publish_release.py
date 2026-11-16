import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

def get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            check=True
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception as e:
        print(f"Error querying git credential: {e}")
    return ""

def main():
    token = get_github_token()
    if not token:
        print("ERROR: Could not retrieve GitHub token.")
        sys.exit(1)

    repo = "Jit-Roy/Phonoryx"
    tag = "v2"
    zip_path = os.path.abspath(r"dist\Phonoryx-v2-windows-x64.zip")
    if not os.path.exists(zip_path):
        print(f"ERROR: {zip_path} not found.")
        sys.exit(1)

    file_size = os.path.getsize(zip_path)
    print(f"Found archive: {zip_path} ({file_size / (1024*1024):.2f} MB)")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Phonoryx-Release-Bot",
    }

    # 1. Check if release already exists
    release_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    req = urllib.request.Request(release_url, headers=headers)
    release = None
    try:
        with urllib.request.urlopen(req) as resp:
            release = json.loads(resp.read().decode())
            print(f"Existing release found for {tag} (ID: {release['id']})")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"HTTP error checking release: {e.code} {e.reason}")
            sys.exit(1)

    release_body = (
        "## Phonoryx v2 (Lite Release)\n\n"
        "Lightweight, real-time, ultra-low latency audio graph routing and sound management engine for Windows.\n\n"
        "### What's New in v2\n"
        "- **Audio Player Node**: Real-time switch-triggered audio playback supporting MP3, WAV, FLAC, and OGG with seamless repeat looping.\n"
        "- **ComfyUI-Style Canvas Copy & Paste**: Full `Ctrl+C` and `Ctrl+V` workflow on canvas with mouse-relative placement and wire reconstruction.\n"
        "- **Intelligent Text-Editing Focus**: Isolated text shortcut handling preventing accidental node selection or deletion while typing.\n"
        "- **Optimized Real-time Engine**: High-capacity source ring buffers (524,288 frames) inside the native C++ WASAPI core for smooth, stutter-free streaming.\n"
        "- **Lightweight Distribution**: Pure standalone bundle (~45 MB) with zero external dependencies."
    )

    if not release:
        create_url = f"https://api.github.com/repos/{repo}/releases"
        body = {
            "tag_name": tag,
            "target_commitish": "main",
            "name": "Phonoryx v2",
            "body": release_body,
            "draft": False,
            "prerelease": False
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(create_url, data=data, headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                release = json.loads(resp.read().decode())
                print(f"Created release {tag} (ID: {release['id']})")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode()
            print(f"Failed to create release: {e.code} {err_msg}")
            sys.exit(1)
    else:
        # Update release description
        patch_url = f"https://api.github.com/repos/{repo}/releases/{release['id']}"
        body = {"body": release_body, "name": "Phonoryx v2"}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(patch_url, data=data, headers={**headers, "Content-Type": "application/json"}, method="PATCH")
        try:
            with urllib.request.urlopen(req) as resp:
                print("Updated release notes on GitHub.")
        except Exception as e:
            print(f"Note update warning: {e}")

    # Check and delete old assets if any
    assets_url = release.get("assets_url", f"https://api.github.com/repos/{repo}/releases/{release['id']}/assets")
    req = urllib.request.Request(assets_url, headers=headers)
    existing_assets = []
    try:
        with urllib.request.urlopen(req) as resp:
            existing_assets = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Could not list assets: {e}")

    asset_name = "Phonoryx-v2-windows-x64.zip"
    for a in existing_assets:
        if a["name"] == asset_name:
            print(f"Deleting existing asset {asset_name} (ID: {a['id']})...")
            del_url = f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}"
            del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
            with urllib.request.urlopen(del_req) as del_resp:
                print(f"Deleted old asset: {del_resp.status}")

    # Upload zip asset
    upload_url_template = release.get("upload_url", "")
    upload_url = upload_url_template.split("{")[0] + f"?name={asset_name}"
    print(f"Uploading {asset_name} ({file_size / (1024*1024):.2f} MB) to {upload_url}...")

    with open(zip_path, "rb") as f:
        file_data = f.read()

    upload_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/zip",
        "Content-Length": str(len(file_data)),
        "User-Agent": "Phonoryx-Release-Bot",
    }
    req = urllib.request.Request(upload_url, data=file_data, headers=upload_headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            asset_info = json.loads(resp.read().decode())
            print(f"Upload successful! Asset ID: {asset_info['id']}")
            print(f"Download URL: {asset_info['browser_download_url']}")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode()
        print(f"Failed to upload asset: {e.code} {err_msg}")
        sys.exit(1)

if __name__ == "__main__":
    main()
