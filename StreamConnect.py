import gi, time
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Gst, GLib, Gio
from threading import Thread, Lock

CLOCK_TIME_NONE = -1
initialized = False
class VideoPlayback:
    def _init_gstreamer():
        global initialized
        if initialized:
            return
        initialized = True
        features = Gst.Registry.get().get_feature_list_by_plugin('v42lcodecs')
        if len(features) == 0:
            print("warning: no v42l codecs found. Make sure to install the correct plugins.")
        for feature in features:
            feature.set_rank(99999999)

    def __init__(self, uri=None, audio_only=False, dirty_timeout=3, popout=False):
        VideoPlayback._init_gstreamer()
        if not ("lock" in self.__dir__()):
            self.lock = Lock()
        self.popout = popout
        self.lock.acquire()
        self.uri = uri
        self.concurrent_callbacks = []
        self.started=False
        self.stop_callbacks =[]
        self.audio_only = audio_only
        self.dirty_timeout = dirty_timeout
        self.dirty_time = 0
        self.needs_background_thread = True
        self.play_rate = 1
        if(uri!=None):
            self.pipeline = self.make_pipeline(uri, audio_only)
            self.pipeline.set_state(Gst.State.NULL)
        else:
            self.pipeline = None
        self.lock.release()

    def make_pipeline(self, uri, audio_only):
        self.pipeline = Gst.Pipeline()

        def make(name, **props):
            keys = []
            values = []
            items = props.items()
            if len(list(items)):
                [*keys],[*values] = zip(*items)
            elem = Gst.ElementFactory.make_with_properties(name, keys, values)
            self.pipeline.add(elem)
            return elem

        decoder = make("uridecodebin3", uri=uri, download=True)

        a_convert = make("audioconvert")
        a_output_queue = make("queue")
        a_sink = make('pulsesink')
        self.a_sink = a_sink
        link = a_convert.link(a_output_queue)
        if not link:
            print('Could not link converter & queue!\n{0}'.format(link))
        link = a_output_queue.link(a_sink)
        if not link:
            print('Could not link queue & pulsesink!\n{0}'.format(link))

        gl_up = None
        if not audio_only:
            gl_up = make("glupload")
            v_convert = make("glcolorconvert")
            link = gl_up.link(v_convert)
            if not link:
                print('Could not link uploader to opengl!\n{0}'.format(link))

            v_sink = make('gtkglsink')
            self.v_sink = v_sink
            link = v_convert.link(v_sink)
            if not link:
                print('Could not link converter & queue!\n{0}'.format(link))

        def on_add_pad(a_convert, v_convert):
            def callback(element, pad):
                string = pad.query_caps(None).to_string()
                if string.startswith('audio'):
                    pad.link(a_convert.get_static_pad('sink'))
                else:
                    if v_convert:
                        pad.link(v_convert.get_static_pad('sink'))
                element.iterate_elements().foreach(lambda x: (print(x), x.iterate_elements().foreach(lambda x: print("   ",x)) if "bin" in str(type(x)).lower() else None))
            return callback
        
        decoder.connect('pad-added', on_add_pad(a_convert, gl_up))
        return self.pipeline

    def set_uri(self, uri, audio_only=False):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            #if we haven't changed modes, we don't need to reconstruct the pipeline
            if self.audio_only == audio_only:
                return
        else:
            self.uri = uri
        self.audio_only = audio_only
        self.pipeline = self.make_pipeline(uri, audio_only)
    
    def get_display(self):

        if self.audio_only:
            self.pipeline.set_state(Gst.State.READY)
            return None

        retval = self.v_sink.get_property("widget")
        self.pipeline.set_state(Gst.State.PLAYING)
        if not self.popout and self.v_sink.get_parent():
            VideoPlayback._delete_parent_widget(retval)
        self.pipeline.set_state(Gst.State.READY)
        return retval

    def play(self):
        self.play_rate = 1
        self.bus = self.pipeline.get_bus()
        if self.started:
            return self.pipeline.set_state(Gst.State.PLAYING)
        else:
            self.bus.add_signal_watch()
            #self.bus.connect('message::eos', self.stop)
            self.started = True
            thread = Thread(target=self._run_concurrent_callbacks)
            thread.start()
            self.background_thread = thread
            return self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.needs_background_thread = False
        for callback, args in self.stop_callbacks:
            callback(*args)

    def pause(self):
        return self.pipeline.set_state(Gst.State.PAUSED)

    def seek_millis(self, time_in_millis):        
        res = self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH|Gst.SeekFlags.ACCURATE, time_in_millis * 1000000)
        return res

    def relative_seek_seconds(self, time_in_seconds):
        succ, cur_time = self.pipeline.query_position(Gst.Format.TIME)
        cur_time += time_in_seconds * 1000000000
        if cur_time<0:
            cur_time=0
        return self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH|Gst.SeekFlags.ACCURATE, cur_time)

    def seek_seconds(self, time_in_seconds):
        return self.seek_millis(time_in_seconds * 1000)

    def add_stop_callback(self, callback, args):
        self.stop_callbacks.append((callback, args))
    
    def get_current_time(self):
        succ, cur_time = self.pipeline.query_position(Gst.Format.TIME)
        return cur_time * .000000001

    def mark_dirty(self):
        self.dirty_time = self.dirty_timeout

    def _delete_parent_widget(widget):
        #By default, gstreamer opens a gtksink widget in a new window. We want to steal the widget and prevent that other window from opening.
        parent_win = widget.get_parent()
        parent_win.remove(widget)
        parent_win.destroy()
        del parent_win

    def _run_concurrent_callbacks(self):
        while True:
            #without a lock here, needs_background_thread could be true, but the pipeline vanishes underneath us due to a call to __init__
            self.lock.acquire()
            if not self.needs_background_thread:
                break
            if self.dirty_time > 0:
                self.dirty_time-=1
            else:
                for callback, args in self.concurrent_callbacks:
                    callback(*args)
            self.lock.release()
            time.sleep(1)

    def clear_concurrent_callbacks(self):
        self.concurrent_callbacks = []

    def add_concurrent_callback(self, callback, args):
        self.concurrent_callbacks.append((callback, args))

    def reconnect(self):
        concurrent_callbacks, stop_callbacks = self.concurrent_callbacks, self.stop_callbacks
        succ, cur_time = self.pipeline.query_position(Gst.Format.TIME)
        #end the previous stream
        old_widget = self.v_sink.get_property("widget")
        self.pipeline.set_state(Gst.State.NULL)
        #create a replacement stream
        self.__init__(uri=self.uri, audio_only=self.audio_only, dirty_timeout=self.dirty_timeout, popout=self.popout)
        self.pipeline.set_state(Gst.State.PAUSED)

        #reattach callbacks
        self.concurrent_callbacks = concurrent_callbacks
        self.stop_callbacks = stop_callbacks

        #get current widget
        self.pipeline.set_state(Gst.State.PLAYING)
        widget = None
        if not self.audio_only:
            widget = self.v_sink.get_property("widget")
            if not self.popout:
                VideoPlayback._delete_parent_widget(widget)

        #seek to the original position
        self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
        self.pipeline.seek(1.0, Gst.Format.TIME, Gst.SeekFlags.FLUSH|Gst.SeekFlags.ACCURATE, Gst.SeekType.SET, cur_time, Gst.SeekType.NONE, CLOCK_TIME_NONE)
        return widget