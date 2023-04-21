from PIL import Image
import gi, sys, os, requests
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GdkPixbuf, GLib, Gst
from multiprocessing.pool import ThreadPool
from threading import Thread

def parallel(func, args):
    if len(args)==0:
        return []
    pool = ThreadPool(processes=min(16, len(args)))
    result = pool.map_async(func, args)
    pool.close()
    pool.join()
    return result.get()

def async_run(func, *args):
    thread = Thread(target=func, args=args)
    thread.start()

def time_format(seconds):
    if seconds < 60:
        return f"0:{seconds:02}"
    minutes = seconds//60
    seconds %= 60
    if minutes < 60:
        return f"{minutes}:{seconds:02}"
    hours = minutes//60
    minutes %= 60
    return f"{hours}:{minutes:02}:{seconds:02}"

def pil_image_to_gtk_image(pil_image):
    #thanks, chatGPT
    width, height = pil_image.size

    data = GLib.Bytes(pil_image.tobytes())
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * 3)

    gtk_image = Gtk.Image.new_from_pixbuf(pixbuf)
    return gtk_image