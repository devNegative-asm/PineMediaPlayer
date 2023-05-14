
from PIL import Image
import gi, sys, os, glob, subprocess
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GdkPixbuf, GLib, Gst
Util = __import__('Util', globals(), locals(), [], 0)
time_format = Util.time_format
pil_image_to_gtk_image = Util.pil_image_to_gtk_image

video_extensions = ['.mp4', '.webm', '.mkv', '.flv', '.avi', '.mov', '.mpg', '.mpv', 'm4v']
audio_extensions = ['.m4a', '.aac', '.flac', '.ogg', '.mp3', '.opus', '.wav']
allowed_extensions = video_extensions + audio_extensions

class FileVideo:
    def __init__(self, path):
        self.path = path
        #generate thumbnail
        if os.path.splitext(path)[1] in video_extensions:
            temp_file = subprocess.run([
                'mktemp',
                '--suffix',
                '.png'], stdout=subprocess.PIPE)
            temp_file_path = temp_file.stdout.decode('utf-8').strip()
            #thinking of using `ffmpeg -i {path} -f apng -` and reading the output straight into a buffer
            subprocess.run([
                "ffmpeg",
                "-i", path,
                "-ss", "00:00:00.000",
                "-vframes", "1",
                "-c", "png",
                temp_file_path,
                "-y"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.thumbnail_uri = temp_file_path
        else:
            self.thumbnail_uri = os.path.join(os.path.dirname(os.path.dirname(__file__)), "play.png")

        vid_len_getter = subprocess.run([
            'ffprobe',
            '-v',
            'error',
            '-show_entries',
            'format=duration',
            '-of',
            'default=noprint_wrappers=1:nokey=1',
            path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.length = int(float(vid_len_getter.stdout.decode('utf-8')))

    def get_length(self):
        return self.length


def stream_connector(default):
    return default

def playtime(vid):
    return time_format(vid.length)

def get_video_data(vid, thumbnail_size):
    tn = thumbnail(vid)
    width, height = tn.size
    if thumbnail_size:
        pil_image = tn.resize((thumbnail_size, int(float(thumbnail_size * height)/width)),resample=Image.BILINEAR)
        width, height = pil_image.size
        new_height = width * 9 // 16
        pil_image = pil_image.crop((0, (height-new_height)//2, width, (height+new_height)//2))
        width, height = pil_image.size
        play_image = Image.open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "play.png")
        ).convert("RGB").resize((width, height),resample=Image.BILINEAR)
        gtk_thumbnail = pil_image_to_gtk_image(Image.blend(pil_image, play_image, -0.2))
    else:
        gtk_thumbnail = None

    return (
        vid,
        f"{os.path.splitext(os.path.basename(vid.path))[0]}\r\n{playtime(vid)} 🕑",
        gtk_thumbnail,
        None
    )

def thumbnail(vid):
    image = Image.open(vid.thumbnail_uri).convert('RGB')
    return image

def search_videos(query, count:int=1):
    if query==None:
        return []
    files = glob.glob(f'{os.path.expanduser("~")}/Videos/**/*.*', recursive=True)
    files = [fil for fil in files if os.path.splitext(fil)[1] in allowed_extensions and query in fil]
    return [FileVideo(path) for path in files[:count]]

def playback_start(vid, video_playback, config):
    video_playback.set_uri("file://" + vid.path, audio_only=config['audio_only_mode'])

def save_video(vid, path):
    raise NotImplementedError("filesys does not support re-saving")

def save_audio(vid, path):
    raise NotImplementedError("filesys does not support re-saving")

def provide_saving():
    return False