"""
Job management for KFX Comic Output plugin.

Dispatches conversions through Calibre's ThreadedJob system (UI stays
responsive, progress shows in the jobs indicator) and converts multiple
books in parallel — the per-book work is dominated by subprocesses and
PIL/zip I/O, so a small thread pool scales well.
"""

import os
import shutil
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

from calibre.gui2 import Dispatcher, error_dialog, info_dialog

from calibre_plugins.kfx_comic_output.i18n import T

# Concurrent book conversions. Each one spawns at most one ebook-convert
# subprocess plus PIL/zip work, so a handful is plenty.
MAX_PARALLEL = 4


def start_conversion(gui):
    """
    Gather selected books and dispatch a background conversion job.

    Args:
        gui: The main Calibre GUI window.
    """
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

    if skipped:
        from calibre.gui2 import warning_dialog
        warning_dialog(
            gui,
            T("Some books skipped"),
            T("The following books were skipped (no supported format):"),
            det_msg="\n".join(skipped),
            show=True,
        )

    from calibre.gui2.threaded_jobs import ThreadedJob

    job = ThreadedJob(
        "kfx_comic_convert",
        T("Converting {n} comic(s) to KFX...").format(n=len(books_to_convert)),
        _run_conversions,
        (books_to_convert,),
        {},
        Dispatcher(partial(_job_finished, gui, books_to_convert)),
        killable=True,
    )
    gui.job_manager.run_threaded_job(job)
    gui.status_bar.show_message(
        T("Converting {n} comic(s) to KFX...").format(n=len(books_to_convert)), 3000)


def _run_conversions(books, abort=None, log=None, notifications=None):
    """Convert books in parallel. Runs in a background job thread."""
    from calibre_plugins.kfx_comic_output.worker import convert_book

    total = len(books)
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, total)) as pool:
        futures = {pool.submit(convert_book, b, log): b for b in books}
        for future in as_completed(futures):
            book = futures[future]
            if abort is not None and abort.is_set():
                for f in futures:
                    f.cancel()
                results.append((book["book_id"], None, T("Cancelled")))
                continue
            try:
                kfx_path = future.result()
                results.append((book["book_id"], kfx_path, None))
            except Exception as e:
                if log is not None:
                    log.error(traceback.format_exc())
                results.append((book["book_id"], None, str(e)))
            done += 1
            if notifications is not None:
                notifications.put((
                    done / total,
                    T("[{i}/{n}] {title}").format(
                        i=done, n=total, title=book["title"]),
                ))

    return results


def _job_finished(gui, books_to_convert, job):
    """Process conversion results in the GUI thread."""
    if job.failed:
        return error_dialog(
            gui, T("Conversion failed"),
            T("Failed to convert {n} book(s):").format(n=len(books_to_convert)),
            det_msg=job.log if isinstance(job.log, str) else "",
            show=True,
        )

    results = job.result
    if results is None:
        return

    db = gui.current_db.new_api
    successes = []
    failures = []

    for book_id, kfx_path, error in results:
        title = "Unknown"
        for b in books_to_convert:
            if b["book_id"] == book_id:
                title = b["title"]
                break

        if error:
            failures.append(T("{title}: {error}").format(title=title, error=error))
            continue

        if kfx_path and os.path.isfile(kfx_path):
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
                # Remove the worker's temp dir (kfx + any intermediates)
                tmp_dir = os.path.dirname(kfx_path)
                if os.path.basename(tmp_dir).startswith("kfx-comic-"):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                else:
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

    gui.library_view.model().refresh()

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
