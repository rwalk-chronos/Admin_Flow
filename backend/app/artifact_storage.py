import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    byte_size: int
    sha256: str


class ArtifactStorage(Protocol):
    def store(self, storage_key: str, source: BinaryIO) -> StoredArtifact: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def delete(self, storage_key: str) -> None: ...


class LocalArtifactStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def store(self, storage_key: str, source: BinaryIO) -> StoredArtifact:
        destination = self._resolve(storage_key)
        temporary = destination.with_name(f"{destination.name}.part")
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            raise FileExistsError(f"Artifact already exists: {storage_key}")

        digest = hashlib.sha256()
        byte_size = 0

        try:
            with temporary.open("xb") as stored_file:
                while chunk := source.read(CHUNK_SIZE):
                    stored_file.write(chunk)
                    digest.update(chunk)
                    byte_size += len(chunk)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return StoredArtifact(byte_size=byte_size, sha256=digest.hexdigest())

    def open(self, storage_key: str) -> BinaryIO:
        return self._resolve(storage_key).open("rb")

    def delete(self, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Storage key must remain within the artifact storage root")
        return candidate


def build_storage_key(event_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    return f"{event_id.hex}/{artifact_id.hex}"
