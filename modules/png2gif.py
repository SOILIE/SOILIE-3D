import os
from PIL import Image

def interpolate_frames(frame1, frame2, num_interpolated_frames):
    """
    Interpolates frames between frame1 and frame2.

    :param frame1: The first frame (PIL Image).
    :param frame2: The second frame (PIL Image).
    :param num_interpolated_frames: The number of frames to interpolate between frame1 and frame2.
    :return: A list of interpolated frames.
    """
    interpolated_frames = []
    for i in range(1, num_interpolated_frames + 1):
        alpha = i / (num_interpolated_frames + 1)
        interpolated_frame = Image.blend(frame1, frame2, alpha)
        interpolated_frames.append(interpolated_frame)
    return interpolated_frames


def animate_output(filename, path, duration_per_frame=400, num_interpolated_frames=5):
    png_files = sorted([os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.png') and
                        '_birds_eye_view' not in f.lower()])
    images = [Image.open(png) for png in png_files]

    all_frames = []
    for i in range(len(images) - 1):
        all_frames.append(images[i])
        interpolated_frames = interpolate_frames(images[i], images[i + 1], num_interpolated_frames)
        all_frames.extend(interpolated_frames)
    all_frames.append(images[-1])

    # Save as an animated GIF
    all_frames[0].save(
        os.path.join(path, os.path.splitext(filename)[0] + '.gif'),
        save_all=True,
        append_images=all_frames[1:],
        duration=int(duration_per_frame/(num_interpolated_frames+1)),
        loop=0)
