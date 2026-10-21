"""Report and teaser files under REPORT_DIRECTORY. The application writes each one once
and never overwrites or deletes a stored file.
"""

import dataclasses
import hashlib
from pathlib import Path

from django.conf import settings

REPORT_FILE_NAME_PREFIX = "report"
# A teaser reaches a prospect as an email attachment, so its name says what the page is.
TEASER_FILE_NAME_PREFIX = "market-preview"


@dataclasses.dataclass(frozen=True, slots=True)
class StoredReportFile:
    path: Path
    file_name: str
    size_bytes: int
    sha256: str


def report_file_path(file_name: str) -> Path:
    return Path(settings.REPORT_DIRECTORY) / file_name


def write_report_file(
    number: int, pdf: bytes, *, file_name_prefix: str
) -> StoredReportFile:
    """Store a new document's PDF as <prefix>-<number>.pdf, refusing to overwrite any
    existing file.
    """
    file_name = f"{file_name_prefix}-{number}.pdf"
    target = report_file_path(file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(
            f"{file_name} already exists; report files are never overwritten."
        )
    # The PDF lands under a temporary name first, so a report file is always complete.
    partial = target.with_name(f"{file_name}.part")
    try:
        with partial.open("xb") as file:
            file.write(pdf)
        partial.rename(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return StoredReportFile(
        path=target,
        file_name=file_name,
        size_bytes=len(pdf),
        sha256=hashlib.sha256(pdf).hexdigest(),
    )
