"""
Job management for KFX Comic Output plugin.

Handles dispatching background conversion jobs through Calibre's
ThreadedJob system and processing results when jobs complete.
"""

import os
import tempfile
import traceback

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.kfx_comic_output.i18n import T


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
            T("No books selected"),
            T("Please select one or more comic/manga books to convert."),
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
        for fmt in ("EPUB", "MOBI", "AZW", "AZW3", "CBZ", "PDF"):
            if fmt in formats:
                source_fmt = fmt
                break

        if source_fmt is None:
            skipped.append(f"{mi.title} {T('(no supported format)')}")
            continue

        # Get the path to the source file
        source_path = db.format_abspath(book_id, source_fmt)
        if not source_path or not os.path.isfile(source_path):
            skipped.append(f"{mi.title} {T('(file not found)')}")
            continue

        books_to_convert.append({
            "book_id": book_id,
            "title": mi.title or "Unknown",
            "author": " & ".join(mi.authors) if mi.authors else "",
            "source_path": source_path,
            "source_fmt": source_fmt,
        })

    if not books_to_convert:
        msg = T("No convertible books found in selection.")
        if skipped:
            msg += "\n\n" + "\n".join(f"  - {s}" for s in skipped)
        return error_dialog(gui, T("Nothing to convert"), msg, show=True)

    # Show warning for skipped books
    if skipped:
        from calibre.gui2 import warning_dialog
        warning_dialog(
            gui,
            T("Some books skipped"),
            T("The following books were skipped (no supported format):"),
            det_msg="\n".join(skipped),
            show=True,
        )

    # Run conversion synchronously with a progress dialog for simplicity
    from calibre.gui2 import Dispatcher
    from qt.core import QProgressDialog, Qt, QApplication

    total = len(books_to_convert)
    progress = QProgressDialog(
        T("Converting {n} comic(s) to KFX...").format(n=total),
        T("Cancelled"),  # cancel button label
        0, total, gui,
    )
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    from calibre_plugins.kfx_comic_output.worker import convert_book

    results = []
    for idx, book_info in enumerate(books_to_convert):
        if progress.wasCanceled():
            results.append((book_info["book_id"], None, T("Cancelled")))
            continue
        progress.setLabelText(
            T("[{i}/{n}] {title}").format(
                i=idx + 1, n=total, title=book_info["title"]
            )
        )
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
            failures.append(T("{title}: {error}").format(title=title, error=error))
            continue

        if kfx_path and os.path.isfile(kfx_path):
            # Add the KFX file as a new format to the book record
            try:
                with open(kfx_path, "rb") as f:
                    db.add_format(book_id, "KFX", f)
                successes.append(title)
            except Exception as e:
                failures.append(
                    T("{title}: {error}").format(
                        title=title,
                        error=T("Failed to add KFX to library: {error}").format(error=e),
                    )
                )
            finally:
                # Clean up the temporary KFX file
                try:
                    os.unlink(kfx_path)
                except OSError:
                    pass
        else:
            failures.append(
                T("{title}: {error}").format(
                    title=title, error=T("KFX output file not found")
                )
            )

    # Refresh the library view to show new formats
    gui.library_view.model().refresh()

    # Build result message
    msg_parts = []
    if successes:
        msg_parts.append(T("Successfully converted {n} book(s):").format(n=len(successes)))
        for t in successes:
            msg_parts.append(f"  - {t}")
    if failures:
        if msg_parts:
            msg_parts.append("")
        msg_parts.append(T("Failed to convert {n} book(s):").format(n=len(failures)))
        for f in failures:
            msg_parts.append(f"  - {f}")

    detail = "\n".join(msg_parts)

    if failures and not successes:
        error_dialog(gui, T("Conversion failed"), detail, show=True)
    elif failures:
        from calibre.gui2 import warning_dialog
        warning_dialog(
            gui,
            T("Conversion partially complete"),
            T("{s} succeeded, {f} failed.").format(s=len(successes), f=len(failures)),
            det_msg=detail,
            show=True,
        )
    else:
        info_dialog(
            gui,
            T("Conversion complete"),
            T("Successfully converted {n} book(s) to KFX.").format(n=len(successes)),
            det_msg=detail,
            show=True,
        )
