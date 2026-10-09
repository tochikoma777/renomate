"""父目录名推断 Provider（SPEC 7.1 顺序 2，置信度 0.6）。

典型布局：`周杰伦/魔杰座/01.mp3` → artist=周杰伦、album=魔杰座。
"""

from __future__ import annotations

from pathlib import Path

from renomate.config import Config
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import register_provider

PATH_CONFIDENCE = 0.60


class PathProvider:
    """从父目录名推断 album / artist。"""

    name = "path"

    def supports(self, profile: str, path: Path) -> bool:
        return len(path.parent.parts) > 0

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        parents = bundle.path.parent.parts
        if not parents:
            return
        if "album" in bundle.fields:
            album = parents[-1].strip()
            if album and album not in {"/", "."}:
                bundle.fields["album"] = FieldValue(album, FieldSource.PATH, PATH_CONFIDENCE)
        if "artist" in bundle.fields and len(parents) >= 2:
            artist = parents[-2].strip()
            if artist and artist not in {"/", "."}:
                bundle.fields["artist"] = FieldValue(artist, FieldSource.PATH, PATH_CONFIDENCE)


register_provider(PathProvider())
