import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import os
Util = __import__('Util', globals(), locals(), [], 0)
async_run=Util.async_run

def open_save_window(parent_window, callback, recommended_name="Video.mp4"):
    action = Gtk.FileChooserAction.SAVE

    dialog = Gtk.FileChooserDialog("Save File",
        parent_window,
        action,
        (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Select", Gtk.ResponseType.OK
        )
    )
    dialog.set_do_overwrite_confirmation(True)
    dialog.set_current_name(recommended_name)
    dialog.set_current_folder(os.path.join(os.path.expanduser("~"),'Videos'))
    res = dialog.run()

    if (res == Gtk.ResponseType.OK):
        filename = dialog.get_filename()
        async_run(callback, filename)
    else:
        print("download cancelled")
    dialog.destroy()