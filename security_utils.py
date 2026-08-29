from __future__ import annotations

import io
import zipfile
from pathlib import Path


class UnsafeUpload(ValueError):
    """Raised when uploaded bytes do not match the declared safe document type."""


OFFICE_MARKERS = {
    ".docx": "word/",
    ".pptx": "ppt/",
}


def validate_document_bytes(
    file_name: str,
    data: bytes,
    *,
    max_bytes: int,
    max_zip_entries: int = 5000,
    max_expanded_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: int = 200,
) -> None:
    """Perform cheap checks before handing untrusted content to a parser.

    The checks reject empty/mismatched files and common ZIP bombs. They are not
    antivirus scanning; deployments handling unknown public uploads should add a
    malware scanner outside the Python process.
    """
    suffix = Path(file_name).suffix.lower()
    if not data:
        raise UnsafeUpload("文件内容为空，请选择有效的课程资料")
    if len(data) > max_bytes:
        raise UnsafeUpload(f"文件过大，当前上限为 {max_bytes // 1024 // 1024}MB")
    if suffix == ".pdf" and not data.lstrip().startswith(b"%PDF-"):
        raise UnsafeUpload("文件扩展名为 PDF，但内容不是有效的 PDF 文件")
    if suffix in {".md", ".txt"}:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsafeUpload("文本文件必须使用 UTF-8 编码") from exc
        return
    if suffix not in OFFICE_MARKERS:
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsafeUpload("Office 文件已损坏，无法安全解析") from exc
    _validate_office_archive(archive, suffix, max_zip_entries, max_expanded_bytes, max_compression_ratio)


def validate_document_path(
    file_name: str,
    path: Path,
    *,
    max_bytes: int,
    max_zip_entries: int = 5000,
    max_expanded_bytes: int = 200 * 1024 * 1024,
    max_compression_ratio: int = 200,
) -> None:
    size = path.stat().st_size
    if size <= 0:
        raise UnsafeUpload("文件内容为空，请选择有效的课程资料")
    if size > max_bytes:
        raise UnsafeUpload(f"文件过大，当前上限为 {max_bytes // 1024 // 1024}MB")
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        with path.open("rb") as source:
            if not source.read(1024).lstrip().startswith(b"%PDF-"):
                raise UnsafeUpload("文件扩展名为 PDF，但内容不是有效的 PDF 文件")
        return
    if suffix in {".md", ".txt"}:
        try:
            with path.open("r", encoding="utf-8-sig") as source:
                while source.read(1024 * 1024):
                    pass
        except UnicodeDecodeError as exc:
            raise UnsafeUpload("文本文件必须使用 UTF-8 编码") from exc
        return
    if suffix in OFFICE_MARKERS:
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise UnsafeUpload("Office 文件已损坏，无法安全解析") from exc
        _validate_office_archive(
            archive, suffix,
            max_zip_entries, max_expanded_bytes, max_compression_ratio,
        )


def _validate_office_archive(
    archive: zipfile.ZipFile,
    suffix: str,
    max_zip_entries: int,
    max_expanded_bytes: int,
    max_compression_ratio: int,
) -> None:
    try:
        with archive:
            infos = archive.infolist()
            if len(infos) > max_zip_entries:
                raise UnsafeUpload("Office 文件内部条目过多，可能已损坏或存在压缩风险")
            names = {item.filename.replace("\\", "/") for item in infos}
            marker = OFFICE_MARKERS[suffix]
            if "[Content_Types].xml" not in names or not any(name.startswith(marker) for name in names):
                raise UnsafeUpload("Office 文件内容与扩展名不匹配")
            expanded = sum(max(0, item.file_size) for item in infos)
            compressed = sum(max(0, item.compress_size) for item in infos)
            if expanded > max_expanded_bytes:
                raise UnsafeUpload("Office 文件解压后体积过大，已停止解析")
            if compressed and expanded / compressed > max_compression_ratio:
                raise UnsafeUpload("Office 文件压缩比异常，已停止解析")
            for item in infos:
                normalized = Path(item.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise UnsafeUpload("Office 文件包含不安全的内部路径")
    except zipfile.BadZipFile as exc:
        raise UnsafeUpload("Office 文件已损坏，无法安全解析") from exc
