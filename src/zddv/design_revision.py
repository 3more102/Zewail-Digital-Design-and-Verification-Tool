from __future__ import annotations

import hashlib

from zddv.config import ProjectConfig


def design_revision_fingerprint(project: ProjectConfig) -> str:
    """Hash the inputs that can change simulator-elaborated design structure."""

    digest = hashlib.sha256()
    digest.update(b"zddv-design-revision-v1\0")
    digest.update(project.top.encode("utf-8"))
    digest.update(b"\0")
    digest.update(project.simulator.encode("utf-8"))
    digest.update(b"\0")

    for group, patterns in (("rtl", project.rtl), ("tb", project.tb)):
        digest.update(group.encode("utf-8"))
        digest.update(b"\0")
        for pattern in patterns:
            digest.update(pattern.encode("utf-8"))
            digest.update(b"\0")

    root = project.root.resolve()
    sources = sorted(
        (path.resolve() for path in project.source_files()),
        key=lambda path: path.as_posix(),
    )
    for source in sources:
        try:
            label = source.relative_to(root).as_posix()
        except ValueError:
            label = source.as_posix()
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(source.read_bytes()).digest())

    return digest.hexdigest()
