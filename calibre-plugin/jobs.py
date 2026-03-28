"""
Job management for KFX Comic Output plugin.

Handles dispatching background conversion jobs through Calibre's
ThreadedJob system and processing results when jobs complete.
"""

import os
import tempfile
import traceback

from calibre.gui2 import error_dialog, info_dialog


def start_conversion(gui):
    """
    Gather selected books and dispatch a background conversion job.

    Args:
        gui: The main Calibre GUI window.
    """
    # Get selected book IDs from the library view
    rows = gui.library_view.selectionModel().selectedRows()
    if not rows:
        return error_dialog(
            gui,
            "No books selected",
            "Please select one or more comic/manga books to convert.",
            show=True,
        )

    book_ids = list(map(gui.library_view.model().id, rows))
    db = gui.current_db.new_api

    # Gather book info for each selected book
    books_to_convert = []
    skipped = []

    for book_id in book_ids:
        mi = db.get_metadata(book_id)
        formats = db.formats(book_id)

        # Find a suitable source format (prefer EPUB, then others)
        source_fmt = None
        for fmt in ("EPUB", "MOBI", "AZW", "AZW3", "CBZ"):
            if fmt in formats:
                source_fmt = fmt
                break

        if source_fmt is None:
            skipped.append(f"{mi.title} (no supported format)")
            continue

        # Get the path to the source file
        source_path = db.format_abspath(book_id, source_fmt)
        if not source_path or not os.path.isfile(source_path):
            skipped.append(f"{mi.title} (file not found)")
            continue

        books_to_convert.append({
            "book_id": book_id,
            "title": mi.title or "Unknown",
            "author": " & ".join(mi.authors) if mi.authors else "",
            "source_path": source_path,
            "source_fmt": source_fmt,
        })

    if not books_to_convert:
        msg = "No convertible books found in selection."
        if skipped:
            msg += "\n\nSkipped:\n" + "\n".join(f"  - {s}" for s in skipped)
        return error_dialog(gui, "Nothing to convert", msg, show=True)

    # Show warning for skipped books
    if skipped:
        from calibre.gui2 import warning_dialog
        warning_dialog(
            gui,
            "Some books skipped",
            "The following books were skipped (no supported format):",
            det_msg="\n".join(skipped),
            show=True,
        )

    # Run conversion synchronously with a progress dialog for simplicity
    from calibre.gui2 import Dispatcher
    from qt.core import QProgressDialog, Qt, QApplication

    total = len(books_to_convert)
    progress = QProgressDialog(
        f"Converting {total} comic(s) to KFX...", "Cancel", 0, total, gui
    )
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    from calibre_plugins.kfx_comic_output.worker import convert_book

    results = []
    for idx, book_info in enumerate(books_to_convert):
        if progress.wasCanceled():
            results.append((book_info["book_id"], None, "Cancelled"))
            continue
        progress.setLabelText(f"[{idx+1}/{total}] {book_info['title']}")
        QApplication.processEvents()
        try:
            kfx_path = convert_book(book_info)
            results.append((book_info["book_id"], kfx_path, None))
        except Exception as e:
            results.append((book_info["book_id"], None, str(e)))
        progress.setValue(idx + 1)

    progress.close()
    _job_finished(gui, results, books_to_convert)



def _job_finished(gui, results, books_to_convert):
    """
    Process conversion results. Adds KFX files to library and reports to user.
    """
    if results is None:
        return

    db = gui.current_db.new_api
    successes = []
    failures = []

    for book_id, kfx_path, error in results:
        # Find the book title from our input list
        title = "Unknown"
        for b in books_to_convert:
            if b["book_id"] == book_id:
                title = b["title"]
                break

        if error:
            failures.append(f"{title}: {error}")
            continue

        if kfx_path and os.path.isfile(kfx_path):
            # Add the KFX file as a new format to the book record
            try:
                with open(kfx_path, "rb") as f:
                    db.add_format(book_id, "KFX", f)
                successes.append(title)
            except Exception as e:
                failures.append(f"{title}: Failed to add KFX to library: {e}")
            finally:
                # Clean up the temporary KFX file
                try:
                    os.unlink(kfx_path)
                except OSError:
                    pass
        else:
            failures.append(f"{title}: KFX output file not found")

    # Refresh the library view to show new formats
    gui.library_view.model().refresh()

    # Build result message
    msg_parts = []
    if successes:
        msg_parts.append(f"Successfully converted {len(successes)} book(s):")
        for t in successes:
            msg_parts.append(f"  - {t}")
    if failures:
        if msg_parts:
            msg_parts.append("")
        msg_parts.append(f"Failed to convert {len(failures)} book(s):")
        for f in failures:
            msg_parts.append(f"  - {f}")

    detail = "\n".join(msg_parts)

    if failures and not successes:
        error_dialog(gui, "Conversion failed", detail, show=True)
    elif failures:
        from calibre.gui2 import warning_dialog
        warning_dialog(
            gui,
            "Conversion partially complete",
            f"{len(successes)} succeeded, {len(failures)} failed.",
            det_msg=detail,
            show=True,
        )
    else:
        info_dialog(
            gui,
            "Conversion complete",
            f"Successfully converted {len(successes)} book(s) to KFX.",
            det_msg=detail,
            show=True,
        )
