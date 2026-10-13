"""Explicitly prepare pinned public model assets; API startup never downloads them.

Downloads use only public Hugging Face fixed-commit URLs. Expected hashes are
pinned in backends.py. A manually transferred offline directory must contain
the same embedding/ and reranker/ layout and exact files.
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from enterprise_agent.rag.backends import (
    MODEL_SPECS,
    ModelFile,
    ModelSpec,
    expected_manifest,
    verified_model_file,
    verify_model_assets,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def prepare_file(root: Path, spec: ModelSpec, file: ModelFile, offline_source: Path | None) -> None:
    target = root / spec.component / file.path
    if not target.resolve().is_relative_to(root):
        raise ValueError("Model destination escapes the configured directory")
    if verified_model_file(target, file):
        print(f"Verified {spec.component}/{file.path}", flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".download")
    if offline_source is not None:
        source = offline_source / spec.component / file.path
        if not source.resolve().is_relative_to(offline_source) or not verified_model_file(
            source, file
        ):
            raise ValueError("Offline source is missing a pinned, valid model file")
        shutil.copyfile(source, temporary)
    else:
        url = f"https://huggingface.co/{spec.repository}/resolve/{spec.revision}/{file.path}"
        request = Request(url, headers={"User-Agent": "EnterpriseAgent-model-prepare/1"})
        received = 0
        last_report = time.monotonic()
        with urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > file.size_bytes:
                    raise ValueError("Model download exceeds pinned file size")
                stream.write(block)
                if time.monotonic() - last_report >= 10:
                    print(
                        f"Downloading {spec.component}/{file.path}: "
                        f"{received}/{file.size_bytes} bytes",
                        flush=True,
                    )
                    last_report = time.monotonic()
    if not verified_model_file(temporary, file):
        raise ValueError("Downloaded model size or SHA256 differs from the pinned artifact")
    temporary.replace(target)
    print(
        f"Prepared {spec.component}/{file.path}: {file.size_bytes} bytes, SHA256 verified",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify pinned Chinese CPU RAG models")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(os.environ.get("EA_RAG_MODEL_DIR", PROJECT_ROOT / "var" / "models")),
        help="Default: EA_RAG_MODEL_DIR or project var/models; private local cache",
    )
    parser.add_argument(
        "--offline-source", type=Path, help="Trusted previously downloaded model directory"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="Check pinned local bytes without downloads"
    )
    args = parser.parse_args()
    root = args.destination.resolve()
    if args.verify_only:
        if not verify_model_assets(root):
            print("Models are absent or failed pinned manifest/SHA256 validation.", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "verified": True,
                    "embedding_fingerprint": expected_manifest()["embedding_fingerprint"],
                }
            )
        )
        return 0
    offline_source = args.offline_source.resolve() if args.offline_source else None
    try:
        root.mkdir(parents=True, exist_ok=True)
        for spec in MODEL_SPECS:
            for file in spec.files:
                prepare_file(root, spec, file, offline_source)
        manifest = expected_manifest() | {"prepared_at": datetime.now(UTC).isoformat()}
        temporary_manifest = root / "manifest.json.download"
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_manifest.replace(root / "manifest.json")
        if not verify_model_assets(root):
            raise ValueError("Final model verification failed")
    except Exception as error:
        # Driver/network text may contain signed redirect URLs: keep it out of output.
        print(
            f"Model preparation failed ({type(error).__name__}); "
            "existing verified files were preserved.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "verified": True,
                "downloaded_model_bytes": sum(
                    file.size_bytes for spec in MODEL_SPECS for file in spec.files
                ),
                "embedding_fingerprint": manifest["embedding_fingerprint"],
                "reranker_score_type": manifest["inference"]["reranker_score_type"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
