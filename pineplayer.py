#!/bin/python3
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GdkPixbuf, GLib, Gst, Gdk

import sys
import os
import json
import httpx
import subprocess
import time
import traceback
import importlib
import argparse

from PIL import Image
from threading import Lock

StreamConnect = __import__('StreamConnect', globals(), locals(), [], 0)
VideoPlayback = StreamConnect.VideoPlayback

SaveWindow = __import__('SaveWindow', globals(), locals(), [], 0)
open_save_window = SaveWindow.open_save_window

Util = __import__('Util', globals(), locals(), [], 0)
parallel, async_run, time_format =(
    Util.parallel, Util.async_run, Util.time_format)

PADDING = 6

CURRENT_MODULE = None
CURRENT_MODULE_NAME = None
def _unimplemented(*args):
    raise NotImplementedError("no module imported.")

get_video_data = _unimplemented
search_videos = _unimplemented
playback_start = _unimplemented
save_video = _unimplemented
save_audio = _unimplemented
provide_saving = _unimplemented
StreamConnector = VideoPlayback

def set_module(mod):
    global CURRENT_MODULE, CURRENT_MODULE_NAME, get_video_data, search_videos, StreamConnector, playback_start, provide_saving, save_audio, save_video
    CURRENT_MODULE = getattr(__import__(f'modules.{mod}', globals(), locals(), [], 0), mod)
    CURRENT_MODULE_NAME = mod
    get_video_data = CURRENT_MODULE.get_video_data
    search_videos = CURRENT_MODULE.search_videos
    StreamConnector = CURRENT_MODULE.stream_connector(default=VideoPlayback)
    playback_start = CURRENT_MODULE.playback_start
    save_video = CURRENT_MODULE.save_video
    save_audio = CURRENT_MODULE.save_audio
    if "provide_saving" in CURRENT_MODULE.__dir__():
        provide_saving = CURRENT_MODULE.provide_saving
    else:
        provide_saving = lambda :False

Gst.init(None)

def connected(widget, event, callback, data):
    widget.connect(event, callback, data)
    return widget

def download_audio(_, data):
    vid, main_grid, win = data
    open_save_window(win, lambda path: UI.add_saving_proc(save_audio(vid, path)), recommended_name=get_video_data(vid, None)[1].replace(" ","_") + '.m4a')

def download_video(_, data):
    vid, main_grid, win = data
    open_save_window(win, lambda path: UI.add_saving_proc(save_video(vid, path)), recommended_name=get_video_data(vid, None)[1].replace(" ","_") + '.webm')

def CSS(css):
    cssProvider = Gtk.CssProvider()
    cssProvider.load_from_data(css.encode('utf-8'))
    return lambda *widgets: [widget.get_style_context().add_provider(cssProvider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION) for widget in widgets]

#global UI items
class MainUI:
    def __init__(self):
        self.view_window = None
        self.main_grid = None
        self.results_menu = None
        self.scroller = None
        self.scroller_handler = None
        self.full_time = 0
        self.search_entry = None
        self.search_button = None
        self.player_controls_hidden = False
        self.settings_bar = None
        self.win = None
        self.last_mouse_down_location = (-1.0, -1.0)
        self.running_videos = []
        self.window_width = 720
        self.window_height = 360
        self.paused=False
        self.saving_subprocs = []
        self.saving_lock = Lock()

    def add_saving_proc(self, process):
        self.saving_lock.acquire()
        self.saving_subprocs.append(process)
        self.saving_lock.release()
    
    def wait_for_saves_to_complete(self):
        for proc in self.saving_subprocs:
            proc.wait()

    #simple helper to distinguish drags (used on mobile for scrolling) from clicks
    def unmoved(self, event):
        xy = (event.x, event.y)
        result = self.last_mouse_down_location==(xy)
        self.last_mouse_down_location = xy
        return result

    def init_popout_window(self, video_player, video_playback):
        popout_window = video_player.get_parent()
        popout_window.set_decorated(False)
        popout_window.set_title("PinePlayer PiP")
        popout_window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        popout_window.connect('button-press-event', lambda win, event: UI.unmoved(event))
        popout_window.connect('button-release-event', on_player_window_clicked, video_playback)
        popout_window.maximize()
        popout_window.fullscreen()
        popout_window.set_keep_above(True)
        popout_window.present()

    def skip(self, delta):
        for running_video, video_player, position, internal_player_box in self.running_videos:
            running_video.relative_seek_seconds(delta)

UI = MainUI()

def size_daemon():
    while 1:
        if UI.win:
            for running_video, video_player, position, internal_player_box in UI.running_videos:
                if internal_player_box: pass
                    #internal_player_box.set_size_request(*size_setting())
                if video_player: pass
                    #video_player.set_size_request(*size_setting())
        if UI.results_menu:
            UI.results_menu.set_size_request(UI.window_width, -1)
        time.sleep(1)
async_run(size_daemon)

def toggle_player_controls():
    UI.player_controls_hidden = not UI.player_controls_hidden
    if UI.player_controls_hidden:
        UI.settings_bar.hide()
        UI.win.fullscreen()
        for running_video, video_player, position, internal_player_box in UI.running_videos:
            internal_player_box.set_margin_bottom(0)
    else:
        UI.settings_bar.show()
        for running_video, video_player, position, internal_player_box in UI.running_videos:
            internal_player_box.set_margin_bottom(250)
        UI.win.unfullscreen()
        UI.win.set_default_size(UI.window_width,UI.window_height)
        UI.win.move(0,0)

def size_setting():
    UI.window_width, UI.window_height = (720, 360)
    if UI.player_controls_hidden:
        desired_width = UI.window_width
        desired_height = UI.window_height
    else:
        desired_width = UI.window_width
        desired_height = UI.window_height - UI.settings_bar.get_allocated_height()
        desired_height = desired_height * 47//62
    if desired_width > desired_height * 16 // 9:
        desired_width = desired_height * 16 // 9
    else:
        desired_height = desired_width * 9 // 16

    return (desired_width, desired_height)

def new_entries_len():
    if provide_saving():
        return 3
    else:
        return 1

def reload_videos():
    replacement_videos = []
    for running_video, video_player, position, internal_player_box in UI.running_videos:
        new_widget = running_video.reconnect()
        old_player = video_player
        if old_player and internal_player_box.get_child() == old_player: internal_player_box.remove(old_player)
        if new_widget:
            if CONFIG['prevent_popout']:
                internal_player_box.add(new_widget)
            else:
                UI.init_popout_window(new_widget, running_video)
            #reconnect maintains callbacks, so no need to copy those
        replacement_videos.append((running_video, new_widget, position, internal_player_box))
    UI.running_videos = replacement_videos

#config
config_path = os.path.join(os.path.dirname(__file__), "config.json")
if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        CONFIG = json.load(f)
else:
    with open(config_path, 'w') as f:
        CONFIG = {
            "audio_only_mode": False,
            "prevent_popout": True
        }
        json.dump(CONFIG, f)

def toggle_config(source, request):
    if type(request) == type(''):
        CONFIG[request] = not CONFIG[request]
        with open(config_path, 'w') as f:
            json.dump(CONFIG, f)
    else:
        data, callback = request
        toggle_config(source, data)
        callback(data)

def stop_all_videos():
    for running_video, video_player, position, internal_player_box in UI.running_videos:
        running_video.stop()
    UI.running_videos = []
    if UI.scroller_handler!=None:
        UI.scroller.disconnect(UI.scroller_handler)
    UI.scroller.hide()
    UI.search_button.show()
    UI.search_entry.show()
    UI.scroller_handler = None

def pause_or_play_from_popout():
    op = (VideoPlayback.pause, VideoPlayback.play)[int(UI.paused)]
    UI.paused = not UI.paused
    for running_video, video_player, position, internal_player_box in UI.running_videos:
        op(running_video)
        parent_window = video_player.get_parent()

def pause_or_play():
    op = (VideoPlayback.pause, VideoPlayback.play)[int(UI.paused)]
    UI.paused = not UI.paused
    for running_video, video_player, position, internal_player_box in UI.running_videos:
        op(running_video)
        parent_window = video_player.get_parent()
        if parent_window and (not UI.paused) and parent_window != internal_player_box:
            parent_window.deiconify()
            parent_window.maximize()
            parent_window.fullscreen()
            parent_window.set_keep_above(True)
            parent_window.present()

def get_subsegment(container, clickEvent):
    x,y = clickEvent.x, clickEvent.y
    wid, hei = container.get_size()
    px = x/wid
    py = y/hei
    return [
        ['TL','TM','TR'],
        ['BL','BM','BR']
    ][(py > 1/2)][(px > 1/3) + (px > 2/3)]

def on_player_window_clicked(window_container, button_event, running_video):
    #window split into segments

    # Unfullscreen           hide           Stop
    #
    # Back 10 seconds        pause          Forward 10 seconds

    location = get_subsegment(window_container, button_event)

    if UI.unmoved(button_event):
        match location:
            case "TM":
                UI.paused = True
                running_video.pause()
                window_container.set_keep_above(False)
                window_container.iconify()
                UI.win.present()
            case "TR":
                stop_all_videos()
            case "TL":
                window_container.unfullscreen()
                dx, dy = size_setting()
                window_container.get_child().set_size_request(dx//2, dy//2)
                window_container.set_size_request(dx//2, dy//2)
                window_container.set_default_size(dx//2, dy//2)
                window_container.set_position(Gtk.WindowPosition.CENTER)
                window_container.set_decorated(not window_container.get_decorated())
                async_run(lambda: (time.sleep(.1), window_container.unmaximize()))
            case "BL":
                UI.skip(-10)
            case "BR":
                UI.skip(10)
            case "BM":
                pause_or_play_from_popout()


def run_search(_, data, existing=None):
    results_count, entry, win= data
    UI.win.set_focus(None)
    UI.window_width, UI.window_height = (720, 360)
    results_count = results_count[0]
    vid_count = 12
    text = entry.get_text()
    results = search_videos(text, count=vid_count)

    stop_all_videos()

    UI.main_grid.remove(UI.view_window)
    UI.view_window.remove(UI.results_menu)
    UI.results_menu.destroy()
    UI.view_window.destroy()

    UI.view_window = Gtk.ScrolledWindow()
    UI.results_menu = Gtk.Grid()
    UI.results_menu.set_column_homogeneous(True)
    UI.results_menu.set_column_spacing(10)
    UI.results_menu.set_row_spacing(10)
    internal_player_box = Gtk.EventBox()
    internal_player_box.set_margin_top(0)
    internal_player_box.set_margin_bottom(250)
    dummy_box = Gtk.Box()
    UI.results_menu.add(dummy_box)
    UI.view_window.add(UI.results_menu)
    UI.main_grid.attach(UI.view_window, 0, 2, 2, 16)

    x_size = UI.window_width//new_entries_len() - PADDING

    all_videos_data = parallel(lambda vid: get_video_data(vid, thumbnail_size=x_size), results)

    for i in range(results_count, results_count + len(results)):
        vid, title_text, gtk_thumbnail, subtext = all_videos_data[i-results_count]
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        titleLabel = Gtk.TextView()
        titleLabel.set_editable(False)
        titleLabel.set_justification(Gtk.Justification.CENTER)
        titleLabel.get_buffer().set_text(title_text)
        titleLabel.set_wrap_mode(Gtk.WrapMode.CHAR)
        titleLabel.set_size_request(x_size, -1)

        if subtext:
            subtext_max_length = 175
            full_subtext = subtext
            if len(subtext) > subtext_max_length:
                subtext = subtext[:170] + '[...]'
            descBox = Gtk.TextView()
            descBox.set_editable(False)
            descBox.set_justification(Gtk.Justification.LEFT)
            descBox.get_buffer().set_text(subtext)
            descBox.set_wrap_mode(Gtk.WrapMode.CHAR)
            descBox.set_size_request(x_size, -1)

        #preview_box.set_size_request(x_size, -1)
        callback_data = (vid, UI.main_grid, win)

        dl_vButton = Gtk.Button(label="⬇📽")
        dl_aButton = Gtk.Button(label="⬇🔊")

        CSS('''
        * {
            font-size: 60px;
        }
        ''')(dl_aButton, dl_vButton)

        video_playback = StreamConnector(dirty_timeout=1, popout=not CONFIG['prevent_popout'])

        gtk_thumbnail.set_size_request(x_size, x_size * 9//16)
        player_box = Gtk.EventBox()
        player_box.add(gtk_thumbnail)

        if provide_saving():
            new_entries = [
                connected(dl_vButton, 'clicked', download_video, callback_data),
                preview_box,
                connected(dl_aButton, 'clicked', download_audio, callback_data)
            ]
        else:
            new_entries = [
                preview_box
            ]
        ypos = i + 1
        #no closures for this function, take all args from req
        def on_video_play(_, __, req):
            video_playback, i, vid, row_elements, video_length = req
            stop_all_videos()
            UI.full_time = video_length
            playback_start(vid, video_playback, CONFIG)
            video_player = video_playback.get_display()
            #video_player is None when we only have audio playback
            if UI.scroller_handler != None:
                UI.scroller.disconnect(UI.scroller_handler)
            UI.scroller.set_range(0, video_length)
            UI.scroller_handler = UI.scroller.connect('change-value', lambda scroller, scrolltype, value, playback: (playback.seek_seconds(value), playback.mark_dirty(), scroller.set_value(value)), video_playback)
            UI.scroller.show()

            if video_player:
                if CONFIG['prevent_popout']:
                    UI.main_grid.remove_row(1)
                    UI.main_grid.remove(UI.view_window)
                    UI.main_grid.attach(internal_player_box, 0, 1, 2, 10)
                    internal_player_box.add(video_player)
                    internal_player_box.connect('button-press-event', lambda box, event: UI.unmoved(event))
                    internal_player_box.connect('button-release-event', lambda box, event:
                        toggle_player_controls() if UI.unmoved(event) else None)

                    video_playback.add_stop_callback(lambda entries, internal_player_box :(
                        internal_player_box.remove(internal_player_box.get_child()),
                        internal_player_box.hide(),
                        UI.main_grid.remove(internal_player_box),
                        UI.main_grid.attach(UI.search_entry, 0, 1, 1, 1),
                        UI.main_grid.attach(UI.search_button, 1, 1, 1, 1),
                        UI.main_grid.attach(UI.view_window, 0, 2, 2, 10),
                        UI.main_grid.show_all()
                    ), (row_elements, internal_player_box))

                    internal_player_box.show()
                else:
                    #audiovideo, popout enabled
                    UI.init_popout_window(video_player, video_playback)
                    

            #i forget why this is necessary, but it is
            video_playback.add_stop_callback(StreamConnector.__init__, (video_playback, None, CONFIG['audio_only_mode'], video_playback.dirty_timeout,not CONFIG['prevent_popout']))

            video_playback.add_concurrent_callback(
                lambda scroller, video_playback: scroller.set_value(video_playback.get_current_time()) if video_playback.get_current_time() else None,
                (UI.scroller, video_playback))
            video_playback.play()
            UI.running_videos.append((video_playback, video_player, (0, i), internal_player_box))
            UI.results_menu.show_all()

        preview_box.add(titleLabel)
        preview_box.add(player_box)
        if subtext:
            preview_box.add(descBox)

        player_box.connect('button-press-event', lambda box, event: UI.unmoved(event))
        player_box.connect('button-release-event', lambda box, event, args:
                on_video_play(box, event, args) if UI.unmoved(event) else None,
            (video_playback, ypos, vid, new_entries, vid.length))

        assert new_entries_len() == len(new_entries)
        for index, entry in enumerate(new_entries):
            entry.set_hexpand(False)
            entry.set_hexpand_set(True)
            UI.results_menu.attach(entry, index, ypos, 1, 1)
            if i==results_count and index==0:
                UI.results_menu.remove(dummy_box)

    UI.main_grid.show_all()
    if UI.scroller_handler==None:
        UI.scroller.hide()
    internal_player_box.hide()

def on_activate_trap_error(app):
    try:
        return on_activate(app)
    except Exception as e:
        print("Exception in main activation thread:")
        print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
        print("contact the developers.")
        os._exit(1)

def load_module_from_dropdown(combobox):
    tree_iter = combobox.get_active_iter()
    if tree_iter is not None:
        model = combobox.get_model()
        ident, name = model[tree_iter][:2]
        set_module(name)
        UI.search_entry.set_text("")
        run_search(None, ([0], UI.search_entry, win))

def on_activate(app):
    win = Gtk.Window(application=app)
    win.set_title("PinePlayer")
    UI.win=win
    win.set_decorated(False)
    win.set_hexpand(False)
    win.set_vexpand(False)
    #pinephone aspect ratio is 1:2

    UI.view_window = Gtk.ScrolledWindow()
    UI.results_menu = Gtk.Grid()
    UI.results_menu.set_column_homogeneous(True)
    UI.results_menu.set_column_spacing(10)
    UI.results_menu.set_row_spacing(10)
    UI.view_window.add(UI.results_menu)
    results_count = [0]

    horiz = True
    UI.window_width = 360
    UI.window_height = 720
    if horiz:
        UI.window_height, UI.window_width = (UI.window_width, UI.window_height)
    win.set_default_size(UI.window_width,UI.window_height)

    entry = Gtk.Entry()
    UI.main_grid = Gtk.Grid()
    btn2 = Gtk.Button(label="🔍")
    request_data = (results_count, entry, win)
    btn2.connect('clicked', run_search, request_data)
    entry.connect('activate', run_search, request_data)
    UI.main_grid.set_column_homogeneous(True)
    UI.main_grid.set_column_spacing(10)
    UI.main_grid.set_row_spacing(2)
    UI.main_grid.set_row_homogeneous(True)
    UI.settings_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

    def novid_label():
        if CONFIG['audio_only_mode']:
            return "🔊"
        else:
            return "📽+🔊"
    novid_button = Gtk.Button(label = novid_label())
    novid_button.connect('clicked', toggle_config, ('audio_only_mode', lambda _: novid_button.set_label(novid_label())))
    UI.settings_bar.add(novid_button)

    stop_button = Gtk.Button(label="🛑")
    stop_button.connect('clicked', lambda *stuff: stop_all_videos())
    UI.settings_bar.add(stop_button)

    toggle_pause_button = Gtk.Button(label="⏯️")
    toggle_pause_button.connect('clicked', lambda *stuff: pause_or_play())
    UI.settings_bar.add(toggle_pause_button)

    skip_back_button = Gtk.Button(label="↤")
    skip_back_button.connect('clicked', lambda *stuff: UI.skip(-10))
    UI.settings_bar.add(skip_back_button)

    skip_forward_button = Gtk.Button(label="↦")
    skip_forward_button.connect('clicked', lambda *stuff: UI.skip(10))
    UI.settings_bar.add(skip_forward_button)

    CSS('''
        * {
            font-size: 20px;
            color: rgba(60,200,255,1);
            font-weight: bold;
        }
    ''')(skip_back_button, skip_forward_button)

    UI.scroller = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
    UI.scroller.set_draw_value(True)
    UI.scroller.connect('format-value', lambda scroller, value: time_format(int(value)) + " / " + time_format(UI.full_time))
    CSS('scale { min-width: ' + str(UI.window_width//3) + 'px; }')(UI.scroller)
    UI.settings_bar.add(UI.scroller)

    reload_button = Gtk.Button(label="⟳")
    reload_button.connect('clicked', lambda *stuff: reload_videos())
    UI.settings_bar.add(reload_button)
    CSS('''
        * {
            font-size: 20px;
            color: rgba(100,255,100,1);
            font-weight: bold;
        }
    ''')(reload_button)

    module_store = Gtk.ListStore(int, str)
    active_module = 0
    for i, moduleName in enumerate(os.listdir(os.path.join(os.path.dirname(__file__), "modules"))):
        if moduleName.endswith('.py'):
            actual_module_name = os.path.splitext(os.path.basename(moduleName))[0]
            module_store.append([i+1, actual_module_name])
            active_module = i
            active_module_name = actual_module_name

    dropdown = Gtk.ComboBox.new_with_model_and_entry(module_store)
    dropdown.connect("changed", load_module_from_dropdown)
    dropdown.set_entry_text_column(1)
    UI.settings_bar.add(dropdown)
    #use the last module by default
    set_module(active_module_name)
    dropdown.set_active(active_module)

    #no need for dummies in gtk4, but then gstreamer doesn't work
    dummy_box = Gtk.Box()
    UI.main_grid.add(dummy_box)
    UI.main_grid.attach(UI.settings_bar, 0, 0, 2, 1)
    UI.main_grid.remove(dummy_box)
    UI.search_button = btn2
    UI.search_entry = entry
    UI.main_grid.attach(entry, 0, 1, 1, 1)
    UI.main_grid.attach(btn2, 1, 1, 1, 1)
    UI.main_grid.attach(UI.view_window, 0, 2, 2, 10)
    win.add(UI.main_grid)
    win.connect('destroy', lambda *args: (stop_all_videos(),UI.wait_for_saves_to_complete(), os._exit(0)))
    win.show_all()
    UI.scroller.hide()

parser = argparse.ArgumentParser(
    prog='PinePlayer',
    description='A GTK-based GUI media player meant to run on the pinephone pro, or similar linux phones.')
args = parser.parse_args()

app = Gtk.Application(application_id='org.gtk.Example')
app.connect('activate', on_activate_trap_error)
app.run(None)
