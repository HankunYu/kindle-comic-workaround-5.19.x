"""
Update checker for the KFX Comic Output plugin.

Queries the GitHub Releases API for the latest release of the repository,
parses the tag into a version tuple, and compares it with the currently
installed plugin version.

Network access is synchronous with a short timeout (5 seconds). Callers
are expected to handle exceptions — this module raises on any failure
(network error, HTTP error, malformed JSON, unparseable tag).
"""

import json
import re
import urllib.error
import urllib.request

from calibre_plugins.kfx_comic_output import __plugin_version__


# GitHub repository coordinates — change here if the repo moves.
REPO_OWNER = "HankunYu"
REPO_NAME = "kindle-comic-workaround-5.19.x"

RELEASES_API = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)
RELEASES_PAGE = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"

# GitHub's API rejects requests without a User-Agent.
_USER_AGENT = "KFX-Comic-Output-Plugin-UpdateCheck"

# Matches tags like "v1.2.3", "V1.2", "1.2.3.4". Trailing suffixes such as
# "-beta" or "+build" are ignored.
_TAG_RE = re.compile(r"^[vV]?(\d+(?:\.\d+){0,3})")


def _parse_version(tag):
    """Parse a tag like 'v1.1.3' into a version tuple (1, 1, 3).

    Raises ValueError if the tag cannot be parsed.
    """
    if not tag:
        raise ValueError("empty tag")
    m = _TAG_RE.match(tag.strip())
    if not m:
        raise ValueError(f"cannot parse version from tag {tag!r}")
    return tuple(int(p) for p in m.group(1).split("."))


def fetch_latest_release(timeout=5):
    """Fetch the latest release from GitHub.

    Returns:
        dict with keys:
            version: tuple[int, ...]       parsed from tag_name
            tag: str                        the raw tag string
            html_url: str                   release page URL
            body: str                       release notes (may be empty)

    Raises:
        urllib.error.URLError / HTTPError on network failure
        ValueError if JSON or tag cannot be parsed
    """
    req = urllib.request.Request(
        RELEASES_API,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON from GitHub: {e}")

    tag = data.get("tag_name") or ""
    version = _parse_version(tag)
    return {
        "version": version,
        "tag": tag,
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "body": data.get("body") or "",
    }


def compare_with_current(latest_version):
    """Return 'newer', 'same', or 'older' relative to the installed version."""
    current = tuple(__plugin_version__)
    if latest_version > current:
        return "newer"
    if latest_version < current:
        return "older"
    return "same"


def format_version(version):
    """Render a version tuple as 'v1.2.3'."""
    return "v" + ".".join(str(p) for p in version)
