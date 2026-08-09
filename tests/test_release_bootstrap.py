from __future__ import annotations

import base64
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rendered_bootstrap_contains_release_defaults(tmp_path: Path) -> None:
    signers = tmp_path / "allowed-signers"
    signers.write_text(
        "release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestReleaseKey\n",
        encoding="utf-8",
    )
    output = tmp_path / "install-botparty.sh"
    commit = "a" * 40
    digest = "b" * 64
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/render-release-bootstrap.py"),
            "--template",
            str(ROOT / "scripts/bootstrap-install.sh"),
            "--output",
            str(output),
            "--ref",
            commit,
            "--allowed-signers",
            str(signers),
            "--bundle",
            f"amd64,https://example.com/bundle.zip,{digest}",
        ],
        check=True,
    )

    rendered = output.read_text(encoding="utf-8")
    assert commit in rendered
    assert "https://example.com/bundle.zip" not in rendered
    assert "__BOTPARTY_RELEASE_REF__" not in rendered
    encoded_bundles = re.search(r'^EMBEDDED_BUNDLES="([^"]+)"$', rendered, re.MULTILINE)
    assert encoded_bundles is not None
    assert base64.b64decode(encoded_bundles.group(1)).decode() == (
        f"amd64\thttps://example.com/bundle.zip\t{digest}"
    )
    assert output.stat().st_mode & 0o111
    subprocess.run(["bash", "-n", str(output)], check=True)
