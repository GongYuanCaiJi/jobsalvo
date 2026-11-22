"""Small PDF operations shared by migration and delivery-package code."""

import pypdf


def merge(paths, destination):
    """Write the pages from each PDF to one PDF, preserving source order."""
    writer = pypdf.PdfWriter()
    readers = [pypdf.PdfReader(path, strict=False) for path in paths]
    for reader in readers:
        writer.append(reader)
    if not writer.pages:
        raise ValueError('沒有可合併的 PDF 頁面')
    with open(destination, 'wb') as f:
        writer.write(f)
